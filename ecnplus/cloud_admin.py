"""Deployment preparation, versioned migrations and explicit local-history import."""

import argparse
import json
import secrets
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet
from psycopg.types.json import Jsonb

from .cloud_storage import Database, PostgresStore, json_value, snapshot_for
from .profiles import PROFILES
from .runtime import PROJECT, env_values, update_env
from .summary import read_settings


def prepare():
    values = env_values(PROJECT / '.env')
    additions = {}
    for key, factory in [('SESSION_SECRET', lambda: secrets.token_urlsafe(48)),
                         ('TOKEN_ENCRYPTION_KEY', lambda: Fernet.generate_key().decode())]:
        if not values.get(key):
            additions[key] = factory()
    path = PROJECT / 'access-codes.json'
    codes = json.loads(path.read_text()) if path.exists() else {}
    for slug in PROFILES:
        key = 'ACCESS_CODE_' + slug.upper()
        if not values.get(key):
            additions[key] = codes.get(slug) or str(secrets.randbelow(900000) + 100000)
    if additions:
        update_env(additions)
    values.update(additions)
    required = ['DATABASE_URL', 'DATABASE_URL_UNPOOLED', 'SESSION_SECRET', 'TOKEN_ENCRYPTION_KEY']
    required += ['ACCESS_CODE_' + slug.upper() for slug in PROFILES]
    if any(not values.get(key) for key in required):
        raise ValueError('Ajoutez les deux URL Neon dans .env avant de préparer le déploiement.')
    Fernet(values['TOKEN_ENCRYPTION_KEY'].encode())
    if len(values['SESSION_SECRET']) < 32 or any(not 4 <= len(values['ACCESS_CODE_' + slug.upper()]) <= 128 for slug in PROFILES):
        raise ValueError('Vérifiez la clé de session et les trois codes d’accès avant le déploiement.')
    update_env({key: values[key] for key in required}, PROJECT / 'deploy.env')
    print('Private deploy.env is ready for Render. Existing settings and keys were preserved; no credentials were printed.')


def import_local(database, slug, data_dir, settings_path):
    """Import into an empty profile only. Never overwrite an existing cloud history."""
    source = sqlite3.connect((Path(data_dir).resolve() / 'source.sqlite3').as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    store = PostgresStore(database, slug)
    try:
        record = database.profile(slug)
        if store.latest_run() or record['latest_complete'] or record['latest_attempt']:
            raise ValueError('Cet espace contient déjà des données. Import annulé pour les préserver.')
        settings = read_settings(settings_path)
        account = source.execute("SELECT value FROM metadata WHERE key='account_id'").fetchone()
        if not account:
            raise ValueError('Le compte associé aux données locales est inconnu.')
        with store.db.transaction():
            store.check_account(account['value'])
            with store.db.cursor() as cursor:
                cursor.executemany('INSERT INTO ecn_runs VALUES (%s,%s,%s,%s,%s)',
                    [(slug, r['id'], r['started_at'], r['finished_at'], r['status']) for r in source.execute('SELECT * FROM runs')])
                cursor.executemany('INSERT INTO ecn_tasks VALUES (%s,%s,%s,%s,%s)',
                    [(slug, r['run_id'], r['path'], r['kind'], Jsonb(json.loads(r['context']))) for r in source.execute('SELECT * FROM tasks')])
                cursor.executemany('INSERT INTO ecn_responses VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    [(slug, r['run_id'], r['path'], r['kind'], Jsonb(json.loads(r['context'])), r['fetched_at'],
                      json_value(json.loads(r['body']) if r['body'] is not None else None), r['error'], r['status_code'])
                     for r in source.execute('SELECT * FROM responses')])
            complete = source.execute("SELECT id FROM runs WHERE status='complete' ORDER BY started_at DESC LIMIT 1").fetchone()
            latest = source.execute('SELECT id FROM runs ORDER BY started_at DESC LIMIT 1').fetchone()
            if not latest:
                raise ValueError('Aucune synchronisation locale à importer.')
            if complete:
                store.publish(snapshot_for(store, complete['id'], settings), settings=settings)
            if not complete or latest['id'] != complete['id']:
                store.publish(snapshot_for(store, latest['id'], settings), settings=settings)
        print('Local study history and reading settings imported. ECNPlus tokens were not imported.')
    finally:
        store.close()
        source.close()


def main():
    parser = argparse.ArgumentParser(description='Prepare ECN Companion for Render and Neon.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare', help='Generate private deployment keys without printing them')
    commands.add_parser('migrate', help='Apply versioned schema migrations using the direct database connection')
    importer = commands.add_parser('import-local', help='Import a local SQLite history into an empty cloud profile')
    importer.add_argument('--profile', choices=PROFILES, required=True)
    importer.add_argument('--data-dir', default=str(PROJECT / 'data'))
    importer.add_argument('--settings', default=str(PROJECT / 'readings.json'))
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare()
    elif args.command == 'migrate':
        Database().migrate()
        print('Database schema is ready.')
    else:
        import_local(Database(), args.profile, args.data_dir, args.settings)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
