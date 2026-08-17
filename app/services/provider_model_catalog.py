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
    "kimi": (
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k2.6",
    ),
    "openai": (
        "gpt-5.6",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.2",
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
    ),
    "anthropic": (
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ),
    "glm": (
        "glm-5.2",
        "glm-5.1",
        "glm-5-turbo",
        "glm-5",
        "glm-4.7",
        "glm-4.7-flash",
        "glm-4.7-flashx",
        "glm-4.6",
        "glm-4.5-air",
        "glm-4.5-airx",
        "glm-4.5-flash",
        "glm-4-flash-250414",
        "glm-4-flashx-250414",
    ),
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
    return tuple(dict.fromkeys((*_CURATED_MODELS.get(normalized, ()), default)))


def _is_translation_model(provider: str, model: str) -> bool:
    if not _MODEL_ID_RE.fullmatch(model):
        return False
    lowered = model.lower()
    if provider == "anthropic":
        return lowered.startswith("claude-")
    if provider == "deepseek":
        retired = {"deepseek-chat", "deepseek-reasoner"}
        return lowered.startswith("deepseek-") and lowered not in retired
    if provider == "kimi":
        retired = {"kimi-k2.5", "kimi-latest", "kimi-thinking-preview"}
        return (
            lowered.startswith("kimi-")
            and lowered not in retired
            and not lowered.startswith("kimi-k2-")
        )
    if provider == "glm":
        excluded = (
            "embedding",
            "rerank",
            "ocr",
            "asr",
            "tts",
            "image",
            "video",
            "vision",
        )
        vision_model = re.match(r"^glm-[0-9]+(?:\.[0-9]+)?v(?:-|$)", lowered)
        return (
            lowered.startswith("glm-")
            and vision_model is None
            and not any(part in lowered for part in excluded)
        )
    if provider == "openai":
        excluded = (
            "audio",
            "chat-latest",
            "chatgpt",
            "codex",
            "deep-research",
            "embedding",
            "image",
            "instruct",
            "preview",
            "pro",
            "realtime",
            "search",
            "transcribe",
            "tts",
            "whisper",
        )
        return lowered.startswith("gpt-") and not any(
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
