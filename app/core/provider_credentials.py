"""Provider catalog and encrypted user credential storage."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.provider_credential import ProviderCredential


class CredentialConfigurationError(RuntimeError):
    """Raised when encrypted credential storage is not configured safely."""


class CredentialDecryptionError(RuntimeError):
    """Raised when a credential cannot be authenticated and decrypted."""


@dataclass(frozen=True)
class ProviderSpec:
    """Fixed, server-controlled provider endpoint and defaults."""

    label: str
    base_url: str
    default_model: str
    protocol: str


@dataclass(frozen=True)
class ResolvedProviderCredential:
    """Decrypted credential used only while executing one translation."""

    provider: str
    api_key: str
    base_url: str
    model: str
    source: str = "personal"


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        default_model="deepseek-v4-pro",
        protocol="deepseek",
    ),
    "kimi": ProviderSpec(
        label="Kimi",
        base_url="https://api.moonshot.cn/v1",
        default_model="kimi-k3",
        protocol="openai-compatible",
    ),
    "openai": ProviderSpec(
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        protocol="openai-compatible",
    ),
    "anthropic": ProviderSpec(
        label="Anthropic",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-sonnet-5",
        protocol="anthropic",
    ),
    "glm": ProviderSpec(
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-5.2",
        protocol="openai-compatible",
    ),
}

_CIPHERTEXT_VERSION = b"\x01"
_NONCE_BYTES = 12


def validate_provider(provider: str) -> str:
    """Normalize and validate a supported user-configurable provider."""
    normalized = provider.strip().lower()
    if normalized not in PROVIDER_SPECS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized


def _master_key() -> bytes:
    encoded = settings.credential_encryption_key.get_secret_value().strip()
    if not encoded:
        raise CredentialConfigurationError(
            "PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY is required to store API keys"
        )
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CredentialConfigurationError(
            "PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY must be a base64url key"
        ) from exc
    if len(key) != 32:
        raise CredentialConfigurationError(
            "PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )
    return key


def _aad(access_scope: str, provider: str) -> bytes:
    return f"paper-china\0{access_scope}\0{provider}".encode("utf-8")


def encrypt_api_key(api_key: str, access_scope: str, provider: str) -> str:
    """Encrypt an API key and bind it to its owner and provider."""
    normalized = validate_provider(provider)
    nonce = os.urandom(_NONCE_BYTES)
    encrypted = AESGCM(_master_key()).encrypt(
        nonce,
        api_key.encode("utf-8"),
        _aad(access_scope, normalized),
    )
    return base64.urlsafe_b64encode(_CIPHERTEXT_VERSION + nonce + encrypted).decode("ascii")


def decrypt_api_key(ciphertext: str, access_scope: str, provider: str) -> str:
    """Authenticate and decrypt a credential for its exact owner/provider pair."""
    normalized = validate_provider(provider)
    try:
        blob = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        if len(blob) <= 1 + _NONCE_BYTES or blob[:1] != _CIPHERTEXT_VERSION:
            raise ValueError("unsupported credential ciphertext")
        nonce = blob[1 : 1 + _NONCE_BYTES]
        plaintext = AESGCM(_master_key()).decrypt(
            nonce,
            blob[1 + _NONCE_BYTES :],
            _aad(access_scope, normalized),
        )
        return plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CredentialDecryptionError(
            "Stored provider credential could not be decrypted"
        ) from exc


async def get_provider_credential_record(
    db: AsyncSession,
    access_scope: str,
    provider: str,
) -> ProviderCredential | None:
    """Load one credential record using both tenant isolation keys."""
    normalized = validate_provider(provider)
    record = await db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.access_scope == access_scope,
            ProviderCredential.provider == normalized,
        )
    )
    # A real SQLAlchemy scalar is either this model or None. Keeping the
    # boundary explicit also prevents malformed repository adapters from being
    # treated as encrypted secret material.
    return record if isinstance(record, ProviderCredential) else None


async def load_provider_credential(
    db: AsyncSession,
    access_scope: str,
    provider: str,
) -> ResolvedProviderCredential | None:
    """Load and decrypt one user-scoped provider credential."""
    normalized = validate_provider(provider)
    record = await get_provider_credential_record(db, access_scope, normalized)
    if record is None:
        return None
    spec = PROVIDER_SPECS[normalized]
    return ResolvedProviderCredential(
        provider=normalized,
        api_key=decrypt_api_key(record.encrypted_api_key, access_scope, normalized),
        base_url=spec.base_url,
        model=record.model or spec.default_model,
    )


def server_provider_credential(provider: str) -> ResolvedProviderCredential | None:
    """Return a legacy server credential for the local administrator only."""
    normalized = validate_provider(provider)
    spec = PROVIDER_SPECS[normalized]
    configured: dict[str, tuple[str, str, str]] = {
        "deepseek": (
            settings.deepseek_api_key.get_secret_value()
            or os.environ.get("DEEPSEEK_API_KEY", ""),
            spec.base_url,
            settings.deepseek_model,
        ),
        "kimi": (
            settings.moonshot_api_key.get_secret_value()
            or os.environ.get("MOONSHOT_API_KEY", ""),
            settings.moonshot_base_url or spec.base_url,
            settings.kimi_model,
        ),
        "openai": (
            settings.openai_api_key.get_secret_value()
            or os.environ.get("OPENAI_API_KEY", ""),
            settings.openai_base_url or spec.base_url,
            settings.openai_model,
        ),
        "anthropic": (
            settings.anthropic_api_key.get_secret_value()
            or os.environ.get("ANTHROPIC_API_KEY", ""),
            spec.base_url,
            settings.anthropic_model,
        ),
        "glm": (
            settings.glm_api_key.get_secret_value() or os.environ.get("GLM_API_KEY", ""),
            spec.base_url,
            settings.glm_model,
        ),
    }
    api_key, base_url, model = configured[normalized]
    if not api_key:
        return None
    return ResolvedProviderCredential(
        provider=normalized,
        api_key=api_key,
        base_url=base_url,
        model=model or spec.default_model,
        source="server",
    )
