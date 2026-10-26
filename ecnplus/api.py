"""Only GET requests to ECNPlus. Credentials never enter stored responses."""

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

API_BASE = "https://api.ecnplus.tn/api/"


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class AuthError(ApiError):
    pass


def authorization(project):
    value = os.environ.get("ECNPLUS_AUTHORIZATION")
    if value is None:
        path = Path(project) / ".env"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                key, separator, content = line.partition("=")
                if separator and key.strip() == "ECNPLUS_AUTHORIZATION":
                    value = content.strip().strip("\"'")
    value = (value or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value or value == "PASTE_TOKEN_HERE" or any(c.isspace() for c in value):
        raise ValueError("Set ECNPLUS_AUTHORIZATION in .env or run: python3 -m ecnplus configure")
    return "Bearer " + value


def tls_context():
    context = ssl.create_default_context()
    # Some macOS Python installations have no configured default CA bundle.
    # Add trusted roots without ever disabling certificate verification.
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    if Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Do not forward the authorization header to a redirect destination.
        return None


class Client:
    def __init__(self, auth, timeout=20, retries=2):
        self.auth = auth
        self.timeout = timeout
        self.retries = retries
        self.context = tls_context()
        self.stopped = threading.Event()
        self.rate_lock = threading.Lock()
        self.next_request = 0.0

    def get(self, path):
        if path.startswith("/") or ":" in path or ".." in path:
            raise ValueError("API path must be relative to the fixed ECNPlus API origin")
        for retry in range(self.retries + 1):
            if self.stopped.is_set():
                raise ApiError("Collection stopped")
            with self.rate_lock:
                delay = max(0.0, self.next_request - time.monotonic())
                if self.stopped.wait(delay):
                    raise ApiError("Collection stopped")
                self.next_request = time.monotonic() + 0.12
            request = urllib.request.Request(
                API_BASE + path,
                headers={"Authorization": self.auth, "Accept": "application/json",
                         "User-Agent": "ECNPlus-local-study-collector/1.0"},
                method="GET",
            )
            opener = urllib.request.build_opener(
                NoRedirect(), urllib.request.HTTPSHandler(context=self.context)
            )
            wait = min(2 ** retry, 10)
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):
                    self.stopped.set()
                    raise AuthError("Authorization rejected; replace the local token", error.code) from None
                if error.code not in (408, 425, 429, 500, 502, 503, 504):
                    raise ApiError("HTTP " + str(error.code), error.code) from None
                retry_after = error.headers.get("Retry-After", "")
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    try:
                        wait = max(wait, parsedate_to_datetime(retry_after).timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
                failure = ApiError("HTTP " + str(error.code), error.code)
            except (urllib.error.URLError, TimeoutError, OSError):
                failure = ApiError("Network or TLS error; check connectivity and trusted certificates")
            except (ValueError, UnicodeError):
                failure = ApiError("API response is not valid JSON")
            if retry == self.retries:
                raise failure
            if self.stopped.wait(min(wait, 30)):
                raise ApiError("Collection stopped")
