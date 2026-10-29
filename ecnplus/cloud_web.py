"""Hosted HTTPS adapter. Assets and profile APIs share one Render origin."""

import os
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from flask import Flask, jsonify, make_response, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .access import Access
from .cloud_storage import CloudBusy, Database
from .profiles import PROFILES
from .runtime import PROJECT, load_env
from .summary import validate_settings
from .web import Dashboard, STATIC


class CloudDashboard(Dashboard):
    def __init__(self, database, slug):
        self.database, self.slug = database, slug
        self.profile = {'slug': slug, 'name': PROFILES[slug]}
        self.record = database.profile(slug)
        self.mutex = threading.RLock()

    def snapshot(self):
        return self.record['latest_complete'] or self.record['latest_attempt']

    def settings(self):
        return self.record['settings']

    def credential_configured(self):
        return bool(self.record['credential'])

    def checkpoint(self):
        return self.database.checkpoint(self.slug)

    def job_state(self):
        return self.database.job(self.slug)


def create_app(config=None, database=None, launch=None):
    load_env()
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=65536)
    app.config.update(config or {})
    secret = app.config.get('SESSION_SECRET') or os.environ.get('SESSION_SECRET', '')
    key = app.config.get('TOKEN_ENCRYPTION_KEY') or os.environ.get('TOKEN_ENCRYPTION_KEY', '')
    if len(secret) < 32:
        raise ValueError('SESSION_SECRET doit contenir au moins 32 caractères.')
    cipher = Fernet(key.encode())
    codes = {slug: app.config.get('ACCESS_CODE_' + slug.upper()) or os.environ.get('ACCESS_CODE_' + slug.upper(), '') for slug in PROFILES}
    if any(len(code) < 4 or len(code) > 128 for code in codes.values()):
        raise ValueError('Définissez les ACCESS_CODE_* de chaque espace avec 4 à 128 caractères.')
    origin = app.config.get('PUBLIC_BASE_URL') or os.environ.get('PUBLIC_BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL', '')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('PUBLIC_BASE_URL doit être l’adresse HTTPS du site.')
    origin = parsed.scheme + '://' + parsed.netloc
    access = Access(codes, secret, secure=True)
    db = database or Database()
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    def launch_worker(slug, job):
        if launch:
            return launch(slug, job)
        try:
            process = subprocess.Popen([sys.executable, '-m', 'ecnplus.cloud_worker', '--profile', slug, '--job-id', job['id']],
                                       cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            db.fail(slug, job['id'], 'Impossible de démarrer la mise à jour. Réessayez.')
            raise ValueError('Impossible de démarrer la mise à jour.') from None

        def reap():
            code = process.wait()
            if code:
                try:
                    db.fail(slug, job['id'], 'La mise à jour a été interrompue. Reprenez la synchronisation.')
                except Exception:
                    pass

        threading.Thread(target=reap, daemon=True).start()

    @app.before_request
    def same_origin():
        if request.path == '/healthz' and request.method == 'GET':
            return None  # Render health probes may use an internal hostname.
        if request.host != parsed.netloc:
            return jsonify(error='Adresse du site invalide.'), 403
        if request.headers.get('Sec-Fetch-Site') == 'cross-site' and '/api/' in request.path:
            return jsonify(error='Ouvrez le site avec son adresse habituelle.'), 403
        if request.method == 'POST' and request.headers.get('Origin') not in (None, origin):
            return jsonify(error='Rechargez la page et réessayez.'), 403

    @app.after_request
    def headers(response):
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Strict-Transport-Security': 'max-age=31536000',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    @app.errorhandler(Exception)
    def error(error):
        if isinstance(error, CloudBusy):
            return jsonify(error=str(error)), 409
        if isinstance(error, ValueError):
            return jsonify(error=str(error)), 400
        if isinstance(error, LookupError):
            return jsonify(error='Cours introuvable.'), 404
        if isinstance(error, HTTPException):
            return jsonify(error='Page introuvable.' if error.code == 404 else 'Requête invalide.'), error.code
        app.logger.error('Hosted request failed (%s)', type(error).__name__)
        return jsonify(error='Le service est momentanément indisponible. Réessayez.'), 503

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/')
    def home():
        return (STATIC / 'profiles.html').read_bytes(), 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.get('/<filename>')
    def asset_or_space(filename):
        if filename in PROFILES:
            return page(filename)
        assets = {'app.js': 'text/javascript', 'styles.css': 'text/css', 'favicon.svg': 'image/svg+xml'}
        if filename not in assets:
            return jsonify(error='Page introuvable.'), 404
        return (STATIC / filename).read_bytes(), 200, {'Content-Type': assets[filename] + '; charset=utf-8'}

    @app.get('/<slug>/')
    def page(slug):
        if slug not in PROFILES:
            return jsonify(error='Espace introuvable.'), 404
        body = (STATIC / 'index.html').read_text()
        body = body.replace('__LOCAL_CSRF__', access.csrf(slug)).replace('__PROFILE_SLUG__', slug).replace('__PROFILE_NAME__', PROFILES[slug])
        return body, 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.route('/<slug>/api/<path:action>', methods=['GET', 'POST'])
    def api(slug, action):
        if slug not in PROFILES:
            return jsonify(error='Espace introuvable.'), 404
        allowed = access.allowed(slug, request.headers.get('Cookie'))
        if request.method == 'GET':
            if action == 'access':
                return jsonify(authorized=allowed)
            if not allowed:
                return jsonify(error='Saisissez le code de cet espace.'), 401
            if action == 'job':
                return jsonify(db.job(slug))
            dashboard = CloudDashboard(db, slug)
            if action == 'dashboard':
                return jsonify(dashboard.payload())
            if action == 'calendar':
                return jsonify(dashboard.calendar())
            if action.startswith('courses/'):
                return jsonify(dashboard.course(action.removeprefix('courses/')))
            return jsonify(error='Action introuvable.'), 404

        import hmac
        if not hmac.compare_digest(request.headers.get('X-ECNPlus-Token', '').encode(), access.csrf(slug).encode()):
            return jsonify(error='Rechargez la page et réessayez.'), 403
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('Format de requête invalide.')
        if action == 'access':
            if not access.check_code(slug, body.get('code'), request.remote_addr):
                return jsonify(error='Code incorrect. Réessayez.'), 401
            response = make_response(jsonify(authorized=True))
            response.headers['Set-Cookie'] = access.cookie(slug)
            return response
        if not allowed:
            return jsonify(error='Saisissez le code de cet espace.'), 401
        if action == 'logout':
            response = make_response(jsonify(authorized=False))
            response.headers['Set-Cookie'] = access.cookie(slug, logout=True)
            return response
        if action == 'credential':
            token = body.get('authorization', '')
            if not isinstance(token, str):
                raise ValueError('Collez votre jeton ECNPlus.')
            token = token.strip()
            if token.lower().startswith('bearer '):
                token = token[7:].strip()
            if not token or any(c.isspace() for c in token) or len(token) > 8192:
                raise ValueError('Collez la valeur Bearer complète ou le jeton seul.')
            db.save_credential(slug, cipher.encrypt(('Bearer ' + token).encode()).decode())
            return jsonify(saved=True)
        if action == 'sync':
            resume = body.get('resume', False)
            if type(resume) is not bool:
                raise ValueError('Option de reprise invalide.')
            if not db.profile(slug)['credential']:
                raise ValueError('Ajoutez votre jeton ECNPlus dans les paramètres.')
            job = db.claim(slug, 'sync', {'resume': resume})
        elif action == 'settings':
            before = db.profile(slug)['settings']
            candidate = validate_settings({**before, 'readings': body.get('readings')})
            job = db.claim(slug, 'settings', {'settings': candidate})
        else:
            return jsonify(error='Action introuvable.'), 404
        launch_worker(slug, job)
        return jsonify(job), 202

    return app
