"""User account helpers: password hashing, token cache, authentication.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib only — the deployment
image stays dependency-free). Login issues the user's long-lived bearer
token, which the access layer resolves to that user's isolated scope.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time

from sqlalchemy import select

from app.core.access import normalize_access_scope
from app.core.database import async_session
from app.models.user import User

_PBKDF2_ITERATIONS = 200_000
_USERNAME_RE = re.compile(r"^[0-9A-Za-z_.-]{1,64}$")

# Token -> scope cache so the per-request auth middleware does not hit SQLite
# on every API call. Short TTL keeps user creation visible quickly; a failed
# login forces a refresh in case a user was just added.
_TOKEN_CACHE_TTL = 15.0
_token_scopes: dict[str, str] = {}
_token_scopes_at = 0.0


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), digest_hex)


def validate_username(username: str) -> str:
    if not _USERNAME_RE.match(username or ""):
        raise ValueError("Username must be 1-64 chars of letters, digits, '.', '_' or '-'")
    return username


async def create_user(username: str, password: str, access_scope: str | None = None) -> User:
    """Create a user with a fresh bearer token. Scope defaults to the username."""
    username = validate_username(username)
    if not password:
        raise ValueError("Password must not be empty")
    scope = normalize_access_scope(access_scope) if access_scope else normalize_access_scope(username)
    user = User(
        username=username,
        password_hash=hash_password(password),
        token=secrets.token_urlsafe(32),
        access_scope=scope,
    )
    async with async_session() as db:
        db.add(user)
        await db.commit()
    await refresh_token_scopes(force=True)
    return user


async def authenticate_user(username: str, password: str) -> User | None:
    """Return the user on valid credentials, else None."""
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


async def refresh_token_scopes(*, force: bool = False) -> dict[str, str]:
    """Return the user token->scope map, reloading from SQLite when stale."""
    global _token_scopes, _token_scopes_at
    now = time.monotonic()
    if not force and _token_scopes_at and (now - _token_scopes_at) < _TOKEN_CACHE_TTL:
        return _token_scopes
    async with async_session() as db:
        result = await db.execute(select(User.token, User.access_scope))
        rows = result.all()
    _token_scopes = {token: scope for token, scope in rows}
    _token_scopes_at = now
    return _token_scopes
