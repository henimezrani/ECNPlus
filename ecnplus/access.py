"""Small per-space code checks and signed, HTTP-only browser cookies."""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from .profiles import PROFILES
from .runtime import PROJECT


class Access:
    def __init__(self, codes, secret=None, secure=False):
        self.codes = codes
        self.secret = (secret or secrets.token_urlsafe(48)).encode()
        self.secure = secure
        self.attempts = {}
        self.mutex = threading.Lock()

    @classmethod
    def local(cls):
        path = PROJECT / 'access-codes.json'
        codes = {slug: str(i + 1) * 4 for i, slug in enumerate(PROFILES)}
        if path.is_file():
            configured = json.loads(path.read_text())
            if not isinstance(configured, dict) or not set(configured).issubset(PROFILES):
                raise ValueError('Vérifiez les espaces dans le fichier des codes d’accès.')
            codes.update(configured)
        if any(not isinstance(v, str) or not v for v in codes.values()):
            raise ValueError('Configurez un code pour chaque espace.')
        return cls(codes)

    def digest(self, value):
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def csrf(self, slug):
        return self.digest('csrf|' + slug)

    def allowed(self, slug, cookie_header):
        try:
            cookies = SimpleCookie()
            cookies.load(cookie_header or '')
            value = cookies['ecn_' + slug].value
            payload, signature = value.rsplit('.', 1)
            if not hmac.compare_digest(signature, self.digest('session|' + payload)):
                return False
            body = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            return (body['slug'] == slug and body['expires'] > time.time() and
                    hmac.compare_digest(body['code'], self.digest('code|' + self.codes[slug])))
        except (KeyError, ValueError, TypeError, UnicodeError, CookieError):
            return False

    def check_code(self, slug, value, address):
        if not isinstance(value, str) or len(value) > 128:
            return False
        with self.mutex:
            key, current = (slug, address), time.monotonic()
            if len(self.attempts) > 2048:
                self.attempts = {k: v for k, v in self.attempts.items() if v and v[-1] > current - 600}
                if len(self.attempts) > 2048:
                    self.attempts.clear()
            attempts = self.attempts.setdefault(key, deque())
            while attempts and attempts[0] < current - 600:
                attempts.popleft()
            if len(attempts) >= 10:
                raise ValueError('Trop d’essais. Réessayez dans dix minutes.')
            valid = hmac.compare_digest(hashlib.sha256(value.strip().encode()).digest(),
                                        hashlib.sha256(self.codes[slug].encode()).digest())
            if valid:
                self.attempts.pop(key, None)
            else:
                attempts.append(current)
            return valid

    def cookie(self, slug, logout=False):
        payload = base64.urlsafe_b64encode(json.dumps({
            'slug': slug, 'expires': int(time.time()) + 86400,
            'code': self.digest('code|' + self.codes[slug]),
        }).encode()).decode().rstrip('=')
        value = '' if logout else payload + '.' + self.digest('session|' + payload)
        cookie = SimpleCookie()
        cookie['ecn_' + slug] = value
        entry = cookie['ecn_' + slug]
        entry['path'], entry['httponly'], entry['samesite'] = '/' + slug, True, 'Strict'
        if self.secure:
            entry['secure'] = True
        if logout:
            entry['max-age'] = 0
        return entry.OutputString()

