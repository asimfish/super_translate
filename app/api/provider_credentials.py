"""Authenticated API for user-scoped translation provider credentials."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import LOCAL_ACCESS_SCOPE, get_request_access_scope
from app.core.database import get_session
from app.core.provider_credentials import (
    PROVIDER_SPECS,
    CredentialConfigurationError,
    encrypt_api_key,
    get_provider_credential_record,
    server_provider_credential,
    validate_provider,
)
from app.models.provider_credential import ProviderCredential

router = APIRouter(prefix="/api/provider-credentials", tags=["provider-credentials"])
AccessScope = Annotated[str, Depends(get_request_access_scope)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")


class ProviderCredentialUpdate(BaseModel):
    """Credential fields users may save; endpoint URLs stay server-controlled."""

    api_key: str | None = Field(default=None, max_length=4096)
    model: str | None = Field(default=None, max_length=100)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and len(value) < 8:
            raise ValueError("API key must be at least 8 characters")
        return value or None

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and not _MODEL_RE.fullmatch(value):
            raise ValueError("Model name contains unsupported characters")
        return value or None


class ProviderCredentialResponse(BaseModel):
    """Safe provider status response that never exposes a stored secret."""

    provider: str
    label: str
    base_url: str
    model: str
    configured: bool
    key_hint: str = ""
    source: str = "none"


def _key_hint(api_key: str) -> str:
    return f"••••{api_key[-4:]}" if api_key else ""


async def _provider_status(
    provider: str,
    db: AsyncSession,
    access_scope: str,
) -> ProviderCredentialResponse:
    spec = PROVIDER_SPECS[provider]
    record = await get_provider_credential_record(db, access_scope, provider)
    if record is not None:
        return ProviderCredentialResponse(
            provider=provider,
            label=spec.label,
            base_url=spec.base_url,
            model=record.model or spec.default_model,
            configured=True,
            key_hint=record.key_hint,
            source="personal",
        )
    server_credential = (
        server_provider_credential(provider) if access_scope == LOCAL_ACCESS_SCOPE else None
    )
    return ProviderCredentialResponse(
        provider=provider,
        label=spec.label,
        base_url=spec.base_url,
        model=(server_credential.model if server_credential else spec.default_model),
        configured=server_credential is not None,
        key_hint="服务器配置" if server_credential else "",
        source="server" if server_credential else "none",
    )


@router.get("", response_model=list[ProviderCredentialResponse])
async def list_provider_credentials(
    db: DbSession,
    access_scope: AccessScope,
) -> list[ProviderCredentialResponse]:
    """List safe configuration status for every supported provider."""
    return [
        await _provider_status(provider, db, access_scope) for provider in PROVIDER_SPECS
    ]


@router.put("/{provider}", response_model=ProviderCredentialResponse)
async def save_provider_credential(
    provider: str,
    payload: ProviderCredentialUpdate,
    db: DbSession,
    access_scope: AccessScope,
) -> ProviderCredentialResponse:
    """Create or update one encrypted provider credential for the current user."""
    try:
        normalized = validate_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    record = await get_provider_credential_record(db, access_scope, normalized)
    if record is None and not payload.api_key:
        raise HTTPException(status_code=400, detail="API key is required for first-time setup")

    spec = PROVIDER_SPECS[normalized]
    if record is None:
        record = ProviderCredential(
            access_scope=access_scope,
            provider=normalized,
            encrypted_api_key="",
            model=payload.model or spec.default_model,
        )
        db.add(record)

    if payload.api_key:
        try:
            record.encrypted_api_key = encrypt_api_key(
                payload.api_key,
                access_scope,
                normalized,
            )
        except CredentialConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        record.key_hint = _key_hint(payload.api_key)
    if payload.model:
        record.model = payload.model
    elif not record.model:
        record.model = spec.default_model

    await db.commit()
    return ProviderCredentialResponse(
        provider=normalized,
        label=spec.label,
        base_url=spec.base_url,
        model=record.model,
        configured=True,
        key_hint=record.key_hint,
        source="personal",
    )


@router.delete("/{provider}")
async def delete_provider_credential(
    provider: str,
    db: DbSession,
    access_scope: AccessScope,
) -> dict[str, bool]:
    """Delete the current user's personal credential for one provider."""
    try:
        normalized = validate_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record = await get_provider_credential_record(db, access_scope, normalized)
    if record is not None:
        await db.delete(record)
        await db.commit()
    return {"ok": True}
