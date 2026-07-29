"""Login endpoints for multi-user accounts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.users import authenticate_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Username/password login payload."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    """Long-lived bearer token for the web UI to cache."""

    token: str
    username: str
    access_scope: str


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> LoginResponse:
    """Exchange credentials for the user's bearer token.

    The token does not expire; the web UI caches it in localStorage so users
    log in once per browser. Rate limiting is handled globally by middleware.
    """
    user = await authenticate_user(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return LoginResponse(
        token=user.token,
        username=user.username,
        access_scope=user.access_scope,
    )
