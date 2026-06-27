"""API access control helpers."""

from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.core.config import settings

LOCAL_ACCESS_SCOPE = "local"
REMOTE_UNAUTHENTICATED_SCOPE = "remote"

_SCOPE_RE = re.compile(r"[^0-9A-Za-z_.-]+")


@dataclass(frozen=True)
class AccessDecision:
    """Result of API authentication and scope resolution."""

    allowed: bool
    scope: str
    authenticated: bool
    status_code: int = 200
    detail: str = ""


def client_host_is_local(host: str) -> bool:
    """Return whether a client host is local/loopback."""
    if host in ("", "unknown", "testclient"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def normalize_access_scope(scope: str) -> str:
    """Normalize a configured workspace name into a stable DB scope value."""
    value = _SCOPE_RE.sub("_", scope.strip()).strip("._-")
    return value[:80] or LOCAL_ACCESS_SCOPE


def workspace_token_scopes(spec: str) -> tuple[tuple[str, str], ...]:
    """Parse PAPER_CHINA_WORKSPACE_TOKENS into (token, scope) pairs.

    Entries are comma/newline separated. Each entry can be ``scope:token``,
    ``scope=token``, or just ``token`` (auto-named workspace_N).
    """
    pairs: list[tuple[str, str]] = []
    seen_tokens: set[str] = set()
    entries = [part.strip() for part in re.split(r"[,\n]+", spec or "") if part.strip()]
    for index, entry in enumerate(entries, start=1):
        if "=" in entry:
            raw_scope, raw_token = entry.split("=", 1)
        elif ":" in entry:
            raw_scope, raw_token = entry.split(":", 1)
        else:
            raw_scope, raw_token = f"workspace_{index}", entry
        token = raw_token.strip()
        if not token or token in seen_tokens:
            continue
        seen_tokens.add(token)
        pairs.append((token, normalize_access_scope(raw_scope)))
    return tuple(pairs)


def configured_token_scopes(
    api_token: str,
    workspace_tokens: str,
) -> tuple[tuple[str, str], ...]:
    """Return all accepted bearer tokens with their access scopes."""
    pairs: list[tuple[str, str]] = []
    if api_token:
        pairs.append((api_token, LOCAL_ACCESS_SCOPE))
    pairs.extend(workspace_token_scopes(workspace_tokens))
    return tuple(pairs)


def bearer_token_from_header(authorization: str) -> str:
    """Extract a bearer token from an Authorization header."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def api_access_decision(
    *,
    authorization: str,
    client_host: str,
    api_token: str,
    workspace_tokens: str,
    allow_unauthenticated_remote: bool,
) -> AccessDecision:
    """Authorize an API request and resolve its workspace scope."""
    token_scopes = configured_token_scopes(api_token, workspace_tokens)
    if token_scopes:
        provided = bearer_token_from_header(authorization)
        for expected, scope in token_scopes:
            if secrets.compare_digest(provided, expected):
                return AccessDecision(allowed=True, scope=scope, authenticated=True)
        return AccessDecision(
            allowed=False,
            scope="",
            authenticated=False,
            status_code=401,
            detail="Invalid or missing API token",
        )

    if not allow_unauthenticated_remote and not client_host_is_local(client_host):
        return AccessDecision(
            allowed=False,
            scope="",
            authenticated=False,
            status_code=403,
            detail="Remote API access requires PAPER_CHINA_API_TOKEN",
        )

    scope = (
        LOCAL_ACCESS_SCOPE
        if client_host_is_local(client_host)
        else REMOTE_UNAUTHENTICATED_SCOPE
    )
    return AccessDecision(allowed=True, scope=scope, authenticated=False)


def access_decision_for_request(request: Request) -> AccessDecision:
    """Resolve access for a FastAPI request using current settings."""
    return api_access_decision(
        authorization=request.headers.get("Authorization", ""),
        client_host=request.client.host if request.client else "",
        api_token=settings.api_token.get_secret_value(),
        workspace_tokens=settings.workspace_tokens,
        allow_unauthenticated_remote=settings.allow_unauthenticated_remote,
    )


def get_request_access_scope(request: Request) -> str:
    """FastAPI dependency returning the current request's isolated scope."""
    decision = access_decision_for_request(request)
    if not decision.allowed:
        raise HTTPException(decision.status_code, decision.detail)
    return decision.scope
