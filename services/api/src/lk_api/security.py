"""Single-user authentication: scrypt password hashes and HMAC-signed session tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from .models import User

COOKIE_NAME = "lk_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(user_id: uuid.UUID, secret: str, hours: int) -> str:
    body = _b64(json.dumps({"sub": str(user_id), "exp": int(time.time()) + hours * 3600}).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str, secret: str) -> uuid.UUID | None:
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_unb64(body))
        if int(claims["exp"]) < time.time():
            return None
        return uuid.UUID(claims["sub"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


class LoginRateLimiter:
    """In-memory limiter: at most ``limit`` failures per ``window`` seconds per key."""

    def __init__(self, limit: int = 10, window: int = 300) -> None:
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            recent = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = recent
            return len(recent) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())


def current_user(request: Request) -> User:
    app = request.app
    token = request.cookies.get(COOKIE_NAME)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    user_id = read_token(token, app.state.settings.secret_key.get_secret_value()) if token else None
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "UNAUTHENTICATED",
                                                                    "message": "Sign in required"})
    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "UNAUTHENTICATED",
                                                                        "message": "Sign in required"})
        session.expunge(user)
        return user


CurrentUser = Depends(current_user)
