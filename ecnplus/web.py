"""Loopback-only dashboard. Standard-library HTTP, saved JSON, and collector jobs."""

import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo

from .api import authorization
from .profiles import default_profiles_root, initialize_profiles
from .storage import Store, lock, now, write_json
from .summary import is_complete, read_settings, validate_settings

PROJECT = Path(__file__).resolve().parent.parent
STATIC = PROJECT / 'dist'


class BusyError(ValueError):
    pass


class Dashboard:
    def __init__(self, profile, profile_dir, data_dir, settings_path):
        self.profile = profile
        self.profile_dir = Path(profile_dir).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.settings_path = Path(settings_path).resolve()
        self.csrf = secrets.token_hex(32)
        self.mutex = threading.RLock()
        self.job = {'status': 'idle', 'kind': None, 'message': '', 'processed': 0, 'total': 0}
        self.process = None
        self.worker = None
        self.closing = False
        self.cached_snapshot = None
        self.cached = None

    def checkpoint(self):
        database = self.data_dir / 'source.sqlite3'
        if not database.exists():
            return None
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT id, status, started_at, finished_at FROM runs ORDER BY started_at DESC LIMIT 1').fetchone()
            return dict(row) if row else None

    def snapshot(self):
        with self.mutex:
            pointer = self.data_dir / 'latest.json'
            if not pointer.exists():
                pointer = self.data_dir / 'latest-attempt.json'
            if not pointer.exists():
                return None
            manifest = json.loads(pointer.read_text(encoding='utf-8'))
            folder = (self.data_dir / manifest['snapshot']).resolve()
            if not folder.is_relative_to((self.data_dir / 'snapshots').resolve()):
                raise ValueError('Emplacement des données enregistrées invalide.')
            if self.cached_snapshot != str(folder):
                self.cached = {'manifest': manifest, 'folder': folder,
                               'summary': json.loads((folder / 'summary.json').read_text(encoding='utf-8')),
                               'collection': json.loads((folder / 'collection.json').read_text(encoding='utf-8'))}
                self.cached_snapshot = str(folder)
            return self.cached

    def payload(self):
        snapshot = self.snapshot()
        settings = read_settings(self.settings_path)
        try:
            configured = bool(authorization(self.profile_dir, allow_environment=False))
        except ValueError:
            configured = False
        summary = snapshot['summary'] if snapshot else None
        saved_settings = summary['settings'] if summary else settings
        today = datetime.now(ZoneInfo('Africa/Tunis')).date().isoformat()
        current = saved_settings['readings'][0]['id']
        for reading in saved_settings['readings'][1:]:
            if reading['starts_on'] and today >= reading['starts_on']:
                current = reading['id']
        collection = snapshot['collection'] if snapshot else None
        return {'profile': {'slug': self.profile['slug'], 'name': self.profile['name']},
                'summary': summary, 'collection': {
                    'last_sync': collection['run'].get('finished_at') if collection else None,
                    'phase_ready': collection.get('phase_ready', False) if collection else False,
                    'source_status': summary['source_status'] if summary else None,
                    'requests_succeeded': collection['requests_succeeded'] if collection else 0,
                    'requests_planned': collection['requests_planned'] if collection else 0,
                    'history_note': collection['history_completeness'] if collection else '',
                }, 'settings': settings, 'settings_changed': settings != saved_settings,
                'current_reading': current, 'credential_configured': configured,
                'checkpoint': self.checkpoint(), 'job': self.job_state()}

    def course(self, course_id):
        snapshot = self.snapshot()
        if not snapshot:
            raise ValueError('Synchronisez d’abord vos données.')
        course = next((c for c in snapshot['summary']['courses'] if c['id'] == course_id), None)
        if not course:
            raise LookupError('Cours introuvable.')
        with self.mutex:
            if 'details' not in snapshot:
                snapshot['details'] = json.loads((snapshot['folder'] / 'details.json').read_text(encoding='utf-8'))
                snapshot['dataset'] = json.loads((snapshot['folder'] / 'dataset.json').read_text(encoding='utf-8'))
        dataset, details = snapshot['dataset'], snapshot['details']
        series = {s['id']: s for s in dataset['series'] if s['course_id'] == course_id}
        selected = {g['selected_attempt_id'] for g in details['groups'] if g['course_id'] == course_id}
        module = next((m['name'] for m in dataset['modules'] if m['_id'] == course['module_id']), None)
        attempts = []
        for attempt in details['attempts']:
            if attempt['course_id'] != course_id:
                continue
            size = series.get(attempt['series_id'], {}).get('question_count')
            url = ('https://app.ecnplus.tn/series/' + '/'.join(quote(p, safe='') for p in
                   [module, course['name'], attempt['series_id']])) if module else None
            attempts.append({'id': attempt['id'], 'series_id': attempt['series_id'],
                             'reading_id': attempt['reading_id'], 'started_at': attempt['created_at'],
                             'answered_questions': attempt['answered_questions'], 'question_count': size,
                             'completed': is_complete(attempt['answered_questions'], size),
                             'included': attempt['included'], 'selected': attempt['id'] in selected,
                             'exclusion_reason': attempt['exclusion_reason'], 'source_url': url})
        attempts.sort(key=lambda a: a['started_at'] or '', reverse=True)
        return {'course': course, 'attempts': attempts, 'settings': snapshot['summary']['settings']}

    def job_state(self):
        with self.mutex:
            return dict(self.job)

    def start(self, kind, operation):
        with self.mutex:
            if self.closing or self.job['status'] == 'running':
                raise BusyError('Une mise à jour est déjà en cours. Veuillez attendre sa fin.')
            self.job = {'status': 'running', 'kind': kind, 'message': 'Préparation de la mise à jour…',
                        'processed': 0, 'total': 0, 'started_at': now()}

            def work():
                try:
                    operation()
                    with self.mutex:
                        self.job.update(status='succeeded', message='Vos données de révision sont à jour.', finished_at=now())
                except Exception as error:
                    with self.mutex:
                        self.job.update(status='failed', message=str(error), finished_at=now())

            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()
            return dict(self.job)

    def synchronize(self, resume=False):
        def operation():
            command = [sys.executable, '-u', '-m', 'ecnplus', '--data-dir', str(self.data_dir),
                       '--settings', str(self.settings_path), '--credential-dir', str(self.profile_dir),
                       '--ignore-environment-token', 'sync']
            if resume:
                command.append('--resume')
            failure = 'La mise à jour n’a pas abouti. Vos données enregistrées restent disponibles. Vous pouvez reprendre la synchronisation.'
            phase = 'Préparation de la mise à jour…'
            with self.mutex:
                if self.closing:
                    raise ValueError('Le tableau de bord se ferme. Les requêtes enregistrées pourront être reprises au prochain démarrage.')
                process = subprocess.Popen(command, cwd=PROJECT, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True,
                                           start_new_session=os.name != 'nt')
                self.process = process
            with process:
                for line in process.stdout:
                    if 'Authorization rejected' in line or 'ECNPLUS_AUTHORIZATION' in line:
                        failure = 'Votre jeton ECNPlus est absent ou expiré. Mettez-le à jour dans les paramètres, puis réessayez.'
                    elif 'Another synchronization' in line:
                        failure = 'Une autre mise à jour est en cours dans un terminal. Attendez sa fin, puis réessayez.'
                    elif 'belongs to another ECNPlus account' in line:
                        failure = 'Ce jeton appartient à un autre compte. Utilisez le jeton associé à cet espace de révision.'
                    if line.startswith('Discovering modules') or line.startswith('Discovering courses'):
                        phase = 'Recherche de vos cours…'
                    elif line.startswith('Discovering series'):
                        phase = 'Mise à jour du catalogue des séries…'
                    elif line.startswith('Checking series'):
                        phase = 'Vérification des informations des séries…'
                    elif line.startswith('Fetching attempt histories'):
                        phase = 'Mise à jour de l’historique des tentatives…'
                    progress = re.search(r'(\d+)/(\d+) processed', line)
                    planned = re.search(r'(\d+) requests: (\d+) saved, (\d+) to fetch', line)
                    with self.mutex:
                        self.job['message'] = phase
                        if planned:
                            self.job.update(processed=0, total=int(planned[3]))
                        if progress:
                            self.job.update(processed=int(progress[1]), total=int(progress[2]))
                code = process.wait()
                with self.mutex:
                    self.process = None
                if code:
                    raise ValueError(failure)
        return self.start('sync', operation)

    def save_settings(self, body):
        current = read_settings(self.settings_path)
        candidate = validate_settings({**current, 'readings': body.get('readings')})

        def operation():
            from .cli import export
            with lock(self.data_dir):
                # Read inside the lock so a terminal edit made before the job starts is preserved.
                before = read_settings(self.settings_path)
                candidate['attempt_reading_overrides'] = before['attempt_reading_overrides']
                validate_settings(candidate)
                snapshot = self.snapshot()
                store = None
                try:
                    write_json(self.settings_path, candidate)
                    if snapshot:
                        store = Store(self.data_dir)
                        export(store, snapshot['manifest']['source_run_id'], candidate, self.data_dir)
                except Exception:
                    write_json(self.settings_path, before)
                    raise
                finally:
                    if store:
                        store.close()
        return self.start('settings', operation)

    def save_credential(self, body):
        value = body.get('authorization', '')
        if not isinstance(value, str):
            raise ValueError('Collez la valeur d’autorisation fournie par ECNPlus.')
        value = value.strip()
        if value.lower().startswith('bearer '):
            value = value[7:].strip()
        if not value or any(c.isspace() for c in value):
            raise ValueError('Collez la valeur Bearer complète ou le jeton seul.')
        with self.mutex:
            if self.job['status'] == 'running':
                raise BusyError('Attendez la fin de la mise à jour avant de modifier le jeton.')
            path = self.profile_dir / '.env'
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            if os.name != 'nt':
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write('ECNPLUS_AUTHORIZATION=Bearer ' + value + '\n')
        return {'saved': True}

    def stop(self):
        with self.mutex:
            self.closing = True
            process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self.worker:
            self.worker.join(timeout=2)


def handler_for(apps):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # No request bodies or credentials in access logs.
            pass

        def allowed_host(self):
            return self.headers.get('Host') in (
                f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def send(self, status, body, content_type='application/json; charset=utf-8'):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def page(self, app):
            body = (STATIC / 'index.html').read_bytes()
            body = body.replace(b'__LOCAL_CSRF__', app.csrf.encode('ascii'))
            body = body.replace(b'__PROFILE_SLUG__', app.profile['slug'].encode('ascii'))
            body = body.replace(b'__PROFILE_NAME__', app.profile['name'].encode('ascii'))
            return self.send(200, body, 'text/html; charset=utf-8')

        def do_GET(self):
            if not self.allowed_host() or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send(403, {'error': 'Ouvrez le tableau de bord avec son adresse locale.'})
            path = urlsplit(self.path).path
            try:
                if path == '/':
                    return self.send(200, (STATIC / 'profiles.html').read_bytes(), 'text/html; charset=utf-8')
                files = {'/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                         '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
                         '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
                if path in files:
                    filename, content_type = files[path]
                    return self.send(200, (STATIC / filename).read_bytes(), content_type)

                parts = path.strip('/').split('/')
                slug = parts[0] if parts else ''
                app = apps.get(slug)
                if app is None:
                    return self.send(404, {'error': 'Espace de révision introuvable.'})
                rest = '/' + '/'.join(parts[1:]) if len(parts) > 1 else '/'
                if rest == '/' or rest == '':
                    return self.page(app)
                if not rest.startswith('/api/'):
                    return self.send(404, {'error': 'Page introuvable.'})
                if rest == '/api/dashboard':
                    return self.send(200, app.payload())
                if rest == '/api/job':
                    return self.send(200, app.job_state())
                if rest.startswith('/api/courses/'):
                    return self.send(200, app.course(unquote(rest.removeprefix('/api/courses/'))))
                return self.send(404, {'error': 'Action introuvable.'})
            except LookupError:
                self.send(404, {'error': 'Cours introuvable.'})
            except (ValueError, OSError, sqlite3.Error):
                self.send(500, {'error': 'Impossible d’ouvrir les données enregistrées. Vérifiez le fichier des paramètres ou lancez une synchronisation.'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            expected_origin = 'http://' + self.headers.get('Host', '')
            if not self.allowed_host() or origin not in (None, expected_origin):
                return self.send(403, {'error': 'Rechargez le tableau de bord et réessayez.'})
            try:
                path = urlsplit(self.path).path
                parts = path.strip('/').split('/')
                slug = parts[0] if parts else ''
                app = apps.get(slug)
                if app is None or len(parts) < 3 or parts[1] != 'api':
                    return self.send(404, {'error': 'Espace de révision introuvable.'})
                if self.headers.get('X-ECNPlus-Token') != app.csrf:
                    return self.send(403, {'error': 'Rechargez le tableau de bord et réessayez.'})
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    raise ValueError('La requête est vide ou trop volumineuse.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Format de requête invalide.')
                action = '/' + '/'.join(parts[1:])
                if action == '/api/sync':
                    if type(body.get('resume', False)) is not bool:
                        raise ValueError('Option de reprise invalide.')
                    return self.send(202, app.synchronize(body.get('resume', False)))
                if action == '/api/settings':
                    return self.send(202, app.save_settings(body))
                if action == '/api/credential':
                    return self.send(200, app.save_credential(body))
                return self.send(404, {'error': 'Action introuvable.'})
            except BusyError as error:
                self.send(409, {'error': str(error)})
            except (ValueError, TypeError, AttributeError) as error:
                self.send(400, {'error': str(error)})
            except (OSError, sqlite3.Error):
                self.send(500, {'error': 'Impossible d’enregistrer la modification. Vos données précédentes restent disponibles.'})

    return Handler


def serve(data_dir, settings_path, port=8765, profiles_root=None):
    profiles = initialize_profiles(profiles_root or default_profiles_root(), data_dir, settings_path, PROJECT)
    apps = {slug: Dashboard(profile, profile['directory'], profile['data_dir'], profile['settings_path'])
            for slug, profile in profiles.items()}
    server = ThreadingHTTPServer(('127.0.0.1', port), handler_for(apps))
    print(f'ECN Companion is ready: http://127.0.0.1:{server.server_port}', flush=True)
    print('Laissez ce terminal ouvert. Appuyez sur Ctrl+C pour arrêter.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for app in apps.values():
            app.stop()
    return 0
