"""Profile-scoped Postgres storage, durable jobs, and bounded raw checkpoints."""

import hashlib
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .profiles import PROFILES
from .runtime import PROJECT, load_env
from .storage import identifier, now
from .summary import read_settings


class CloudBusy(ValueError):
    pass


def lock_key(profile):
    return int.from_bytes(hashlib.sha256(('ecnplus:' + profile).encode()).digest()[:8], 'big', signed=True)


def json_value(value):
    return Jsonb(value) if value is not None else None


def public_job(row):
    return {k: v for k, v in row.items() if k not in ('profile', 'lease_until', 'payload')}


class Database:
    def __init__(self, url=None, direct_url=None):
        import os
        load_env()
        self.url = url or os.environ.get('DATABASE_URL')
        self.direct_url = direct_url or os.environ.get('DATABASE_URL_UNPOOLED') or self.url
        if not self.url:
            raise ValueError('DATABASE_URL est requis pour le mode hébergé.')
        if '-pooler' in (urlsplit(self.direct_url).hostname or ''):
            raise ValueError('DATABASE_URL_UNPOOLED doit utiliser la connexion Neon directe, sans -pooler.')

    def connect(self, direct=False):
        return psycopg.connect(self.direct_url if direct else self.url, autocommit=True,
                               connect_timeout=15, row_factory=dict_row)

    def migrate(self):
        with self.connect(direct=True) as db, db.transaction():
            db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key('schema'),))
            db.execute('CREATE TABLE IF NOT EXISTS ecn_schema (version integer PRIMARY KEY, digest text NOT NULL)')
            for path in sorted((PROJECT / 'migrations').glob('[0-9][0-9][0-9]_*.sql')):
                version = int(path.name.split('_', 1)[0])
                source = path.read_text()
                digest = hashlib.sha256(source.encode()).hexdigest()
                previous = db.execute('SELECT digest FROM ecn_schema WHERE version=%s', (version,)).fetchone()
                if previous and previous['digest'] != digest:
                    raise ValueError('La migration existante a été modifiée. Ajoutez une nouvelle migration.')
                if not previous:
                    db.execute(source, prepare=False)
                    db.execute('INSERT INTO ecn_schema VALUES (%s, %s)', (version, digest))
            settings = read_settings(PROJECT / 'readings.json')
            for slug in PROFILES:
                db.execute('INSERT INTO ecn_profiles(slug, settings) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                           (slug, Jsonb(settings)))

    def profile(self, slug):
        with self.connect() as db:
            return db.execute('SELECT * FROM ecn_profiles WHERE slug=%s', (slug,)).fetchone()

    def checkpoint(self, slug):
        with self.connect() as db:
            return db.execute('SELECT id,status,started_at,finished_at FROM ecn_runs WHERE profile=%s ORDER BY started_at DESC LIMIT 1',
                              (slug,)).fetchone()

    def job(self, slug):
        with self.connect() as db, db.transaction():
            row = db.execute('SELECT *, lease_until < now() AS expired FROM ecn_jobs WHERE profile=%s', (slug,)).fetchone()
            if not row:
                return {'status': 'idle', 'kind': None, 'message': '', 'processed': 0, 'total': 0}
            expired = row.pop('expired')
            if row['status'] == 'running' and expired:
                free = db.execute('SELECT pg_try_advisory_xact_lock(%s) AS free', (lock_key(slug),)).fetchone()['free']
                if free:
                    db.execute("UPDATE ecn_jobs SET status='failed', message=%s, finished_at=%s, lease_until=NULL WHERE profile=%s AND id=%s AND lease_until < now()",
                               ('La mise à jour a été interrompue. Reprenez la synchronisation.', now(), slug, row['id']))
                    db.execute("UPDATE ecn_runs SET status='interrupted', finished_at=%s WHERE profile=%s AND status='running'", (now(), slug))
                    row = db.execute('SELECT * FROM ecn_jobs WHERE profile=%s', (slug,)).fetchone()
            return public_job(row)

    def claim(self, slug, kind, payload=None):
        with self.connect() as db, db.transaction():
            self.available(db, slug)
            job_id = identifier()
            row = db.execute("""INSERT INTO ecn_jobs(profile,id,kind,status,message,started_at,lease_until,payload)
                VALUES (%s,%s,%s,'running',%s,%s,now()+interval '120 seconds',%s)
                ON CONFLICT(profile) DO UPDATE SET id=excluded.id,kind=excluded.kind,status=excluded.status,
                message=excluded.message,started_at=excluded.started_at,finished_at=NULL,processed=0,total=0,
                lease_until=excluded.lease_until,payload=excluded.payload RETURNING *""",
                (slug, job_id, kind, 'Préparation de la mise à jour…', now(), Jsonb(payload or {}))).fetchone()
            return public_job(row)

    @staticmethod
    def available(db, slug):
        free = db.execute('SELECT pg_try_advisory_xact_lock(%s) AS free', (lock_key(slug),)).fetchone()['free']
        running = db.execute("SELECT 1 FROM ecn_jobs WHERE profile=%s AND status='running' AND lease_until > now()", (slug,)).fetchone()
        if not free or running:
            raise CloudBusy('Une mise à jour est déjà en cours dans cet espace.')

    def save_credential(self, slug, encrypted):
        with self.connect() as db, db.transaction():
            self.available(db, slug)
            db.execute('UPDATE ecn_profiles SET credential=%s WHERE slug=%s', (encrypted, slug))

    def heartbeat(self, slug, job_id):
        with self.connect() as db:
            return db.execute("UPDATE ecn_jobs SET lease_until=now()+interval '120 seconds' WHERE profile=%s AND id=%s AND status='running'",
                              (slug, job_id)).rowcount == 1

    def fail(self, slug, job_id, message):
        with self.connect() as db:
            db.execute("UPDATE ecn_jobs SET status='failed', message=%s, finished_at=%s, lease_until=NULL WHERE profile=%s AND id=%s AND status='running'",
                       (message, now(), slug, job_id))


class PostgresStore:
    """The collector's existing Store contract, isolated by profile in every query.

    The dedicated direct connection owns a session advisory lock for its entire
    lifetime. Losing that connection stops all writes; it is never reconnected.
    """
    def __init__(self, database, profile, job_id=None):
        self.database, self.profile, self.job_id = database, profile, job_id
        self.db = database.connect(direct=True)
        self.runs = {}
        self.saved = {}
        self.processed = self.total = 0
        try:
            if not self.db.execute('SELECT pg_try_advisory_lock(%s) AS free', (lock_key(profile),)).fetchone()['free']:
                raise CloudBusy('Une autre mise à jour est déjà en cours.')
            if job_id:
                row = self.db.execute("SELECT * FROM ecn_jobs WHERE profile=%s AND id=%s AND status='running'", (profile, job_id)).fetchone()
                if not row:
                    raise CloudBusy('Cette mise à jour a été remplacée.')
                self.job = row
            else:
                self.job = None
            row = self.db.execute('SELECT latest_complete FROM ecn_profiles WHERE slug=%s', (profile,)).fetchone()
            self.baseline = row['latest_complete']['dataset'] if row and row['latest_complete'] else None
        except BaseException:
            self.close()
            raise

    def close(self):
        self.db.close()  # Releases the session advisory lock, including on process death.

    def check_account(self, account_id):
        row = self.db.execute('SELECT account_id FROM ecn_profiles WHERE slug=%s', (self.profile,)).fetchone()
        if row['account_id'] and row['account_id'] != account_id:
            raise ValueError('Ce jeton appartient à un autre compte ECNPlus que les données de cet espace.')
        self.db.execute('UPDATE ecn_profiles SET account_id=%s WHERE slug=%s', (account_id, self.profile))

    def start(self, resume=False):
        previous = self.latest_run()
        if resume:
            if not previous or previous['status'] == 'complete':
                raise ValueError('Aucune synchronisation interrompue à reprendre.')
            run_id = previous['id']
            self.db.execute("UPDATE ecn_runs SET status='running',finished_at=NULL WHERE profile=%s AND id=%s", (self.profile, run_id))
        else:
            run_id = identifier()
            self.db.execute("INSERT INTO ecn_runs VALUES (%s,%s,%s,NULL,'running')", (self.profile, run_id, now()))
        self.runs.pop(run_id, None)
        return run_id

    def finish(self, run_id, status):
        self.db.execute('UPDATE ecn_runs SET status=%s,finished_at=%s WHERE profile=%s AND id=%s',
                        (status, now(), self.profile, run_id))
        self.runs.pop(run_id, None)

    def save(self, run_id, task, body=None, error=None, status_code=None):
        self.db.execute("""INSERT INTO ecn_responses VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(profile,run_id,path) DO UPDATE SET kind=excluded.kind,context=excluded.context,
            fetched_at=excluded.fetched_at,body=excluded.body,error=excluded.error,status_code=excluded.status_code""",
            (self.profile, run_id, task['path'], task['kind'], Jsonb(task['context']), now(), json_value(body), error, status_code))
        if run_id in self.saved:
            self.saved[run_id][task['path']] = body if error is None else None
        self.processed += 1
        if self.job_id:
            self.db.execute('UPDATE ecn_jobs SET processed=%s WHERE profile=%s AND id=%s',
                            (self.processed, self.profile, self.job_id))

    def enqueue(self, run_id, tasks):
        with self.db.cursor() as cursor:
            cursor.executemany('INSERT INTO ecn_tasks VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                               [(self.profile, run_id, t['path'], t['kind'], Jsonb(t['context'])) for t in tasks])
        self.processed = 0
        self.total = sum(self.response(run_id, t['path']) is None for t in tasks)
        if self.job_id:
            phase = {'modules': 'Recherche des modules…', 'courses': 'Recherche de vos cours…',
                     'series': 'Mise à jour du catalogue…', 'results': 'Mise à jour du catalogue…',
                     'detail': 'Vérification des séries…', 'history': 'Mise à jour des tentatives…'}
            self.db.execute('UPDATE ecn_jobs SET message=%s,processed=0,total=%s WHERE profile=%s AND id=%s',
                            (phase.get(tasks[0]['kind'], 'Mise à jour…') if tasks else 'Mise à jour…', self.total, self.profile, self.job_id))

    def tasks(self, run_id):
        return self.db.execute("""SELECT t.run_id,t.path,t.kind,t.context,r.fetched_at,r.error,r.status_code,
            r.body IS NOT NULL AND r.error IS NULL AS success FROM ecn_tasks t LEFT JOIN ecn_responses r
            USING(profile,run_id,path) WHERE t.profile=%s AND t.run_id=%s ORDER BY t.kind,t.path""",
            (self.profile, run_id)).fetchall()

    def response(self, run_id, path):
        if run_id not in self.saved:
            self.saved[run_id] = {row['path']: row['body'] for row in self.db.execute(
                'SELECT path,body FROM ecn_responses WHERE profile=%s AND run_id=%s AND error IS NULL',
                (self.profile, run_id))}
        return self.saved[run_id].get(path)

    def responses(self, run_id=None):
        sql = """SELECT r.run_id,r.path,r.kind,r.context,r.fetched_at,r.body,r.error,r.status_code
            FROM ecn_responses r JOIN ecn_runs n ON (n.profile=r.profile AND n.id=r.run_id)
            WHERE r.profile=%s"""
        params = [self.profile]
        if run_id:
            sql += ' AND r.run_id=%s'
            params.append(run_id)
        yield from self.db.execute(sql + ' ORDER BY n.started_at,r.fetched_at,r.path', params).fetchall()

    def latest_run(self):
        return self.db.execute('SELECT id,started_at,finished_at,status FROM ecn_runs WHERE profile=%s ORDER BY started_at DESC LIMIT 1',
                               (self.profile,)).fetchone()

    def run(self, run_id):
        if run_id not in self.runs:
            self.runs[run_id] = self.db.execute('SELECT id,started_at,finished_at,status FROM ecn_runs WHERE profile=%s AND id=%s',
                                               (self.profile, run_id)).fetchone()
        return self.runs[run_id]

    def seed(self, run_id):
        if self.baseline and self.baseline['run']['started_at'] <= self.run(run_id)['started_at']:
            return self.baseline
        return None

    def publish(self, snapshot, settings=None, job_status='succeeded', message='Vos données de révision sont à jour.'):
        manifest = snapshot['manifest'] if snapshot else None
        with self.db.transaction():
            row = self.db.execute('SELECT latest_attempt FROM ecn_profiles WHERE slug=%s FOR UPDATE', (self.profile,)).fetchone()
            previous = row['latest_attempt']
            if snapshot and (not previous or previous['manifest']['source_started_at'] <= manifest['source_started_at']):
                self.db.execute('UPDATE ecn_profiles SET latest_attempt=%s WHERE slug=%s', (Jsonb(snapshot), self.profile))
            if snapshot and manifest['source_status'] == 'complete':
                self.db.execute('UPDATE ecn_profiles SET latest_complete=%s WHERE slug=%s', (Jsonb(snapshot), self.profile))
                # The merged dataset is the history seed. Old raw runs are now redundant.
                self.db.execute('DELETE FROM ecn_runs WHERE profile=%s AND started_at < %s',
                                (self.profile, manifest['source_started_at']))
            if settings is not None:
                self.db.execute('UPDATE ecn_profiles SET settings=%s WHERE slug=%s', (Jsonb(settings), self.profile))
            if self.job_id:
                changed = self.db.execute("UPDATE ecn_jobs SET status=%s,message=%s,finished_at=%s,lease_until=NULL WHERE profile=%s AND id=%s AND status='running'",
                                          (job_status, message, now(), self.profile, self.job_id)).rowcount
                if not changed:
                    raise CloudBusy('La mise à jour n’est plus active.')
        if snapshot and manifest['source_status'] == 'complete':
            self.baseline = snapshot['dataset']


def snapshot_for(store, run_id, settings):
    from .cli import build_report
    dataset, result, collection = build_report(store, run_id, settings)
    return {
        'manifest': {'schema_version': 1, 'source_run_id': run_id,
                     'source_status': dataset['run']['status'],
                     'source_started_at': dataset['run']['started_at'],
                     'generated_at': collection['generated_at'], 'phase_ready': collection['phase_ready']},
        'dataset': dataset,
        'summary': {k: v for k, v in result.items() if k not in ('issues', 'groups', 'attempts', 'collection')},
        'details': {'groups': result['groups'], 'attempts': result['attempts']},
        'collection': collection,
    }
