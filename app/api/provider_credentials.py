"""Authenticated API for user-scoped translation provider credentials."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import LOCAL_ACCESS_SCOPE, get_request_access_scope
from app.core.database import get_session
from app.core.provider_credentials import (
    PROVIDER_SPECS,
    CredentialConfigurationError,
    CredentialDecryptionError,
    encrypt_api_key,
    get_provider_credential_record,
    load_provider_credential,
    server_provider_credential,
    validate_provider,
)
from app.models.provider_credential import ProviderCredential
from app.services.provider_model_catalog import (
    MODEL_CATALOG_VERIFIED_ON,
    ProviderModelCatalog,
    get_provider_model_catalog,
    provider_model_guidance,
)

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


class ProviderModelOptionResponse(BaseModel):
    """Display guidance for one model without changing its API identifier."""

    id: str
    group: str
    description: str


class ProviderModelCatalogResponse(BaseModel):
    """Safe provider model choices; API keys are intentionally absent."""

    provider: str
    label: str
    default_model: str
    selected_model: str
    models: list[str]
    model_options: list[ProviderModelOptionResponse]
    source: str
    refreshed_at: datetime | None = None
    catalog_verified_on: date
    warning: str = ""


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


async def _catalog_credential(
    provider: str,
    db: AsyncSession,
    access_scope: str,
):
    try:
        credential = await load_provider_credential(db, access_scope, provider)
    except (CredentialConfigurationError, CredentialDecryptionError):
        credential = None
    if credential is None and access_scope == LOCAL_ACCESS_SCOPE:
        credential = server_provider_credential(provider)
    return credential


def _catalog_response(
    provider: str,
    catalog: ProviderModelCatalog,
    selected_model: str,
) -> ProviderModelCatalogResponse:
    spec = PROVIDER_SPECS[provider]
    models = list(dict.fromkeys((selected_model, *catalog.models)))
    model_options = []
    for model in models:
        group, description = provider_model_guidance(provider, model)
        model_options.append(
            ProviderModelOptionResponse(
                id=model,
                group=group,
                description=description,
            )
        )
    return ProviderModelCatalogResponse(
        provider=provider,
        label=spec.label,
        default_model=spec.default_model,
        selected_model=selected_model,
        models=models,
        model_options=model_options,
        source=catalog.source,
        refreshed_at=catalog.refreshed_at,
        catalog_verified_on=MODEL_CATALOG_VERIFIED_ON,
        warning=catalog.warning,
    )


@router.get("/models", response_model=list[ProviderModelCatalogResponse])
async def list_provider_models(
    db: DbSession,
    access_scope: AccessScope,
) -> list[ProviderModelCatalogResponse]:
    """List model choices and lazily refresh configured providers every six hours."""
    credentials = {}
    for provider in PROVIDER_SPECS:
        credentials[provider] = await _catalog_credential(provider, db, access_scope)

    catalogs = await asyncio.gather(
        *(
            get_provider_model_catalog(
                provider,
                access_scope=access_scope,
                api_key=(credentials[provider].api_key if credentials[provider] else None),
                base_url=(credentials[provider].base_url if credentials[provider] else None),
            )
            for provider in PROVIDER_SPECS
        )
    )
    return [
        _catalog_response(
            provider,
            catalog,
            credentials[provider].model
            if credentials[provider]
            else PROVIDER_SPECS[provider].default_model,
        )
        for provider, catalog in zip(PROVIDER_SPECS, catalogs)
    ]


@router.post(
    "/models/{provider}/refresh",
    response_model=ProviderModelCatalogResponse,
)
async def refresh_provider_models(
    provider: str,
    db: DbSession,
    access_scope: AccessScope,
) -> ProviderModelCatalogResponse:
    """Force-refresh one provider catalog after the user saves a credential."""
    try:
        normalized = validate_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    credential = await _catalog_credential(normalized, db, access_scope)
    if credential is None:
        raise HTTPException(status_code=400, detail="请先保存该供应商的 API Key")
    catalog = await get_provider_model_catalog(
        normalized,
        access_scope=access_scope,
        api_key=credential.api_key,
        base_url=credential.base_url,
        force_refresh=True,
    )
    return _catalog_response(normalized, catalog, credential.model)


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
