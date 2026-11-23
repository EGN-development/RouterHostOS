"""Local administrator bootstrap and signed, CSRF-protected browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any


class AuthError(ValueError):
    pass


class AuthStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.users_path = self.directory / "users.json"
        self.key_path = self.directory / "session.key"
        if not self.key_path.exists():
            self._atomic_write(self.key_path, secrets.token_bytes(32), binary=True)
        os.chmod(self.key_path, 0o600)
        self.key = self.key_path.read_bytes()
        if len(self.key) != 32:
            raise AuthError("invalid session signing key")

    def initialized(self) -> bool:
        return self.users_path.exists()

    def bootstrap(self, username: str, password: str) -> None:
        if self.initialized():
            raise AuthError("administrator is already initialized")
        if not username or len(username) > 64 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in username):
            raise AuthError("username must contain 1-64 letters, digits, dots, underscores, or hyphens")
        if len(password) < 14 or len(password) > 1024:
            raise AuthError("password must be between 14 and 1024 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
        record = {
            "username": username,
            "role": "admin",
            "password_salt": base64.b64encode(salt).decode(),
            "password_hash": base64.b64encode(digest).decode(),
        }
        self._atomic_write(self.users_path, json.dumps(record).encode())

    def verify(self, username: str, password: str) -> bool:
        try:
            record = json.loads(self.users_path.read_text())
            salt = base64.b64decode(record["password_salt"], validate=True)
            expected = base64.b64decode(record["password_hash"], validate=True)
            actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
        except (OSError, KeyError, ValueError, TypeError):
            return False
        return hmac.compare_digest(record["username"], username) and hmac.compare_digest(expected, actual)

    def create_session(self, username: str, lifetime: int = 3600) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(24)
        payload = {
            "sub": username,
            "role": "admin",
            "exp": int(time.time()) + lifetime,
            "csrf": csrf,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
        signature = base64.urlsafe_b64encode(hmac.new(self.key, encoded.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        return f"{encoded}.{signature}", csrf

    def read_session(self, token: str | None) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        encoded, signature = token.rsplit(".", 1)
        expected = base64.urlsafe_b64encode(hmac.new(self.key, encoded.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        if not hmac.compare_digest(expected, signature):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("role") != "admin" or not isinstance(payload.get("sub"), str) or not isinstance(payload.get("csrf"), str) or not isinstance(payload.get("exp"), int) or payload["exp"] <= int(time.time()):
            return None
        return payload

    def _atomic_write(self, path: Path, data: bytes | str, binary: bool = False) -> None:
        raw = data if isinstance(data, bytes) else data.encode()
        fd, temp_name = tempfile.mkstemp(prefix=".auth-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, path)
            dir_fd = os.open(self.directory, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
