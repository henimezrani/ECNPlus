"""A resumable collector child process; all authoritative state lives in Neon."""

import argparse
import os
import signal
import threading

from cryptography.fernet import Fernet, InvalidToken

from .api import ApiError, AuthError, Client
from .cli import collect
from .cloud_storage import Database, PostgresStore, snapshot_for
from .profiles import PROFILES
from .summary import validate_settings


def run_worker(database, slug, job_id):
    stopped, lost = threading.Event(), threading.Event()
    client = None

    def heartbeat():
        while not stopped.wait(20):
            try:
                if not database.heartbeat(slug, job_id):
                    raise ValueError('Job replaced')
            except Exception:
                lost.set()
                if client:
                    client.stopped.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    store = None
    try:
        store = PostgresStore(database, slug, job_id)
        thread.start()
        record = database.profile(slug)
        if store.job['kind'] == 'settings':
            settings = validate_settings(store.job['payload']['settings'])
            snapshot = record['latest_complete'] or record['latest_attempt']
            snapshot = snapshot_for(store, snapshot['manifest']['source_run_id'], settings) if snapshot else None
            if lost.is_set():
                raise ValueError('La mise à jour a été interrompue. Réessayez.')
            store.publish(snapshot, settings=settings)
            return 0

        try:
            token = Fernet(os.environ['TOKEN_ENCRYPTION_KEY'].encode()).decrypt(record['credential'].encode()).decode()
        except (InvalidToken, TypeError, AttributeError):
            raise ValueError('Le jeton enregistré est illisible. Mettez-le à jour dans les paramètres.') from None
        client = Client(token, timeout=20, retries=2)
        identity = client.get('auth/profile')
        if not isinstance(identity, dict) or not isinstance(identity.get('_id'), str):
            raise ValueError('Impossible de vérifier le compte ECNPlus.')
        store.check_account(identity['_id'])
        run_id = store.start(store.job['payload'].get('resume', False))
        status = 'partial'
        message = 'La synchronisation est incomplète. Reprenez-la pour récupérer les requêtes manquantes.'
        try:
            collect(store, run_id, client, workers=3)
            tasks = store.tasks(run_id)
            if tasks and all(t['success'] for t in tasks):
                status = 'complete'
        except AuthError:
            status = 'auth_failed'
            message = 'Votre jeton ECNPlus a expiré. Mettez-le à jour dans les paramètres, puis reprenez la synchronisation.'
        except (ApiError, KeyboardInterrupt):
            status = 'interrupted'
        finally:
            store.finish(run_id, status)
        if lost.is_set():
            raise ValueError('La connexion à la base a été interrompue. Reprenez la synchronisation.')
        store.publish(snapshot_for(store, run_id, record['settings']),
                      job_status='succeeded' if status == 'complete' else 'failed',
                      message='Vos données de révision sont à jour.' if status == 'complete' else message)
        return 0 if status == 'complete' else 2
    except AuthError:
        database.fail(slug, job_id, 'Votre jeton ECNPlus est absent ou expiré. Mettez-le à jour dans les paramètres.')
        return 1
    except ValueError as error:
        database.fail(slug, job_id, str(error))
        return 1
    except BaseException:
        # Do not expose database URLs, encrypted credentials or provider exceptions.
        try:
            database.fail(slug, job_id, 'La mise à jour a été interrompue. Vos requêtes enregistrées pourront être reprises.')
        except Exception:
            pass  # An expired lease is recovered on the next dashboard request.
        return 1
    finally:
        stopped.set()
        if client:
            client.stopped.set()
        if store:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=PROFILES, required=True)
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    return run_worker(Database(), args.profile, args.job_id)


if __name__ == '__main__':
    raise SystemExit(main())
