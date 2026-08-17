"""Provider model discovery with scoped, short-lived caching."""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from app.core.provider_credentials import PROVIDER_SPECS, validate_provider

MODEL_CATALOG_TTL_SECONDS = 6 * 60 * 60
MODEL_DISCOVERY_TIMEOUT_SECONDS = 8
_MAX_RESPONSE_BYTES = 1024 * 1024
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")

# These are the offline-safe choices. Authenticated discovery augments them with
# the models actually available to the current provider account.
_CURATED_MODELS: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
    "kimi": ("kimi-k3",),
    "openai": ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"),
    "anthropic": ("claude-sonnet-5",),
    "glm": ("glm-5.2", "glm-5-turbo", "glm-4.7"),
}


@dataclass(frozen=True)
class ProviderModelCatalog:
    """Safe model identifiers and refresh metadata for one provider."""

    provider: str
    models: tuple[str, ...]
    source: str
    refreshed_at: datetime | None = None
    warning: str = ""


@dataclass(frozen=True)
class _CacheEntry:
    models: tuple[str, ...]
    refreshed_at: datetime
    expires_at: float


_catalog_cache: dict[tuple[str, str], _CacheEntry] = {}
_cache_lock = Lock()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep provider credentials bound to the configured models endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def curated_provider_models(provider: str) -> tuple[str, ...]:
    """Return deterministic fallback models, always including the default."""
    normalized = validate_provider(provider)
    default = PROVIDER_SPECS[normalized].default_model
    return tuple(dict.fromkeys((default, *_CURATED_MODELS.get(normalized, ()))))


def _is_translation_model(provider: str, model: str) -> bool:
    if not _MODEL_ID_RE.fullmatch(model):
        return False
    lowered = model.lower()
    if provider == "anthropic":
        return lowered.startswith("claude-")
    if provider == "deepseek":
        return lowered.startswith("deepseek-")
    if provider == "kimi":
        return lowered.startswith(("kimi-", "moonshot-"))
    if provider == "glm":
        excluded = ("embedding", "rerank", "ocr", "asr", "tts", "image", "video")
        return lowered.startswith("glm-") and not any(part in lowered for part in excluded)
    if provider == "openai":
        excluded = (
            "audio",
            "embedding",
            "image",
            "realtime",
            "search",
            "transcribe",
            "tts",
            "whisper",
        )
        return lowered.startswith(("gpt-", "o1", "o3", "o4")) and not any(
            part in lowered for part in excluded
        )
    return False


def fetch_provider_models(
    provider: str,
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: int = MODEL_DISCOVERY_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """Fetch model IDs from a trusted provider endpoint without retaining the key."""
    normalized = validate_provider(provider)
    headers = {"Accept": "application/json", "User-Agent": "paper-china/model-catalog"}
    if normalized == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers=headers,
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout_seconds) as response:
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Provider model response is too large")
    payload = json.loads(raw.decode("utf-8"))
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    models = {
        entry.get("id", "").strip()
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    return tuple(sorted(model for model in models if _is_translation_model(normalized, model)))


async def get_provider_model_catalog(
    provider: str,
    *,
    access_scope: str,
    api_key: str | None,
    base_url: str | None = None,
    force_refresh: bool = False,
) -> ProviderModelCatalog:
    """Resolve a user-scoped catalog, refreshing stale authenticated entries lazily."""
    normalized = validate_provider(provider)
    curated = curated_provider_models(normalized)
    if not api_key:
        return ProviderModelCatalog(normalized, curated, "curated")

    cache_key = (access_scope, normalized)
    now = time.monotonic()
    if not force_refresh:
        with _cache_lock:
            cached = _catalog_cache.get(cache_key)
        if cached is not None and cached.expires_at > now:
            return ProviderModelCatalog(
                normalized,
                cached.models,
                "cache",
                refreshed_at=cached.refreshed_at,
            )

    try:
        discovered = await asyncio.to_thread(
            fetch_provider_models,
            normalized,
            api_key=api_key,
            base_url=base_url or PROVIDER_SPECS[normalized].base_url,
        )
        if not discovered:
            raise ValueError("Provider returned no compatible translation models")
    except (OSError, TimeoutError, UnicodeDecodeError, ValueError):
        return ProviderModelCatalog(
            normalized,
            curated,
            "curated",
            warning="无法更新模型列表，已使用内置列表",
        )

    models = tuple(dict.fromkeys((*curated, *discovered)))
    refreshed_at = datetime.now(timezone.utc)
    entry = _CacheEntry(
        models=models,
        refreshed_at=refreshed_at,
        expires_at=now + MODEL_CATALOG_TTL_SECONDS,
    )
    with _cache_lock:
        _catalog_cache[cache_key] = entry
    return ProviderModelCatalog(
        normalized,
        models,
        "provider",
        refreshed_at=refreshed_at,
    )
