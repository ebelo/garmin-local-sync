"""Authenticated, rate-limited Garmin Personal API transport."""

import base64
import contextlib
import email.utils
import hashlib
import json
import math
import stat
import time
from pathlib import Path

import requests

from .config import BASE, DEFAULT_KEY, TOKEN_PATH
from .util import atomic_write, dumps


class ApiError(RuntimeError):
    def __init__(self, status, path):
        self.status = status
        super().__init__(f"Garmin HTTP {status}: {path}")


class Api:
    def __init__(self, key_file=DEFAULT_KEY, delay=1.0):
        self.key_file = Path(key_file).expanduser()
        mode = self.key_file.stat().st_mode
        if not stat.S_ISREG(mode):
            raise RuntimeError("API key must be a regular file")
        if mode & 0o077:
            raise RuntimeError("API key must be private; use chmod 600 on the key file")
        self.key = self.key_file.read_text(encoding="utf-8-sig").strip()
        if not self.key or "\n" in self.key or "\r" in self.key:
            raise RuntimeError("Invalid API key file format")
        self.fingerprint = hashlib.sha256(self.key.encode()).hexdigest()
        self.cache = self.key_file.parent / "local-sync-bearer.json"
        self.token = None
        self.expires = 0
        self.delay = delay
        self.last_request = 0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "garmin-local-sync/0.1 (personal use)"
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            cached = json.loads(self.cache.read_text())
            if (
                cached["fingerprint"] == self.fingerprint
                and isinstance(cached["access_token"], str)
                and isinstance(cached["expires_at"], (int, float))
                and math.isfinite(cached["expires_at"])
            ):
                self.token, self.expires = cached["access_token"], cached["expires_at"]

    def _request(self, method, path, **kwargs):
        # Fixed HTTPS origin and no redirects: credentials cannot follow a redirect.
        for attempt in range(7):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                r = self.session.request(
                    method, BASE + path, timeout=(15, 90), allow_redirects=False, **kwargs
                )
            except requests.RequestException:
                if attempt == 6:
                    raise RuntimeError(f"Network failure calling Garmin {path}") from None
                time.sleep(min(2**attempt, 30))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 6 or (r.status_code >= 500 and attempt >= 2):
                    raise ApiError(r.status_code, path)
                retry = r.headers.get("Retry-After", "")
                try:
                    wait = float(retry)
                except ValueError:
                    try:
                        wait = email.utils.parsedate_to_datetime(retry).timestamp() - time.time()
                    except (ValueError, TypeError):
                        wait = min(2 ** (attempt + 1), 60)
                if not math.isfinite(wait) or wait < 0:
                    wait = min(2 ** (attempt + 1), 60)
                if wait > 300:
                    raise RuntimeError("Garmin requested a long cooldown; rerun later to resume")
                print(f"Garmin HTTP {r.status_code}; retrying in {max(0, wait):.0f}s", flush=True)
                time.sleep(max(0, wait))
                continue
            return r
        raise RuntimeError("Retry budget exhausted")

    def authenticate(self, force=False):
        if not force and self.token and self.expires > time.time() + 300:
            return
        r = self._request("POST", TOKEN_PATH, headers={"Authorization": "ApiKey " + self.key})
        if r.status_code != 200:
            raise ApiError(r.status_code, TOKEN_PATH)
        token = r.text.strip()
        try:
            payload = token.split(".")[1]
            expiry = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
            if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time():
                raise ValueError("Invalid token expiry")
        except (ValueError, KeyError, IndexError):
            raise RuntimeError("Garmin did not return a valid token response") from None
        self.token, self.expires = token, expiry
        atomic_write(
            self.cache,
            dumps({"fingerprint": self.fingerprint, "access_token": token, "expires_at": expiry}).encode(),
        )

    def get(self, path, params=None, binary=False):
        self.authenticate()
        for attempt in range(2):
            r = self._request("GET", path, params=params, headers={"Authorization": "Bearer " + self.token})
            if r.status_code == 401 and attempt == 0:
                self.authenticate(force=True)
                continue
            if r.status_code == 204 and not binary:
                return None
            if r.status_code != 200:
                raise ApiError(r.status_code, path)
            if binary:
                return r.content
            try:
                return r.json()
            except ValueError:
                raise RuntimeError(f"Garmin returned invalid JSON for {path}") from None
