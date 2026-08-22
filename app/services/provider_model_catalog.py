"""Provider model discovery with scoped, short-lived caching."""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import Lock
from urllib.parse import urlencode

from app.core.provider_credentials import PROVIDER_SPECS, validate_provider

MODEL_CATALOG_TTL_SECONDS = 6 * 60 * 60
MODEL_DISCOVERY_TIMEOUT_SECONDS = 8
MODEL_CATALOG_VERIFIED_ON = date(2026, 8, 23)
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_MODEL_PAGES = 10
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

_MODEL_GUIDANCE: dict[str, dict[str, tuple[str, str]]] = {
    "deepseek": {
        "deepseek-v4-pro": ("latest", "旗舰质量优先"),
        "deepseek-v4-flash": ("economy", "低成本高速"),
    },
    "kimi": {
        "kimi-k3": ("latest", "最新通用旗舰"),
        "kimi-k2.6": ("balanced", "通用能力均衡"),
        "kimi-k2.7-code": ("specialized", "代码专项模型"),
        "kimi-k2.7-code-highspeed": ("specialized", "高速代码专项模型"),
    },
    "openai": {
        "gpt-5.6": ("latest", "最新旗舰"),
        "gpt-5.6-terra": ("balanced", "效果、速度与成本均衡"),
        "gpt-5.6-luna": ("economy", "低成本高速"),
        "gpt-5-mini": ("economy", "轻量低成本"),
        "gpt-5-nano": ("economy", "最低成本"),
        "gpt-4.1-mini": ("economy", "轻量兼容模型"),
        "gpt-4.1-nano": ("economy", "低成本兼容模型"),
        "gpt-4o-mini": ("economy", "低成本兼容模型"),
    },
    "anthropic": {
        "claude-fable-5": ("latest", "最新通用旗舰"),
        "claude-opus-5": ("quality", "复杂任务质量优先"),
        "claude-sonnet-5": ("balanced", "效果与速度均衡"),
        "claude-haiku-4-5": ("economy", "低成本高速"),
    },
    "glm": {
        "glm-5.2": ("latest", "最新旗舰，超长上下文"),
        "glm-5.1": ("balanced", "上一代旗舰"),
        "glm-5-turbo": ("balanced", "高速通用模型"),
        "glm-4.7-flash": ("economy", "低成本高速"),
        "glm-4.7-flashx": ("economy", "高速增强版"),
        "glm-4.5-air": ("economy", "轻量低成本"),
        "glm-4.5-airx": ("economy", "轻量高速"),
        "glm-4.5-flash": ("legacy", "官方标记即将下线"),
        "glm-4-flash-250414": ("legacy", "固定版本兼容模型"),
        "glm-4-flashx-250414": ("legacy", "固定版本兼容模型"),
    },
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


def provider_model_guidance(provider: str, model: str) -> tuple[str, str]:
    """Return stable UI guidance while leaving account-only models selectable."""
    normalized = validate_provider(provider)
    guidance = _MODEL_GUIDANCE.get(normalized, {}).get(model)
    if guidance is not None:
        return guidance
    if model in curated_provider_models(normalized):
        return ("legacy", "兼容模型")
    return ("account", "当前账号可用")


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

    opener = urllib.request.build_opener(_NoRedirectHandler())
    models_url = f"{base_url.rstrip('/')}/models"
    models: set[str] = set()
    seen_cursors: set[str] = set()
    cursor = ""
    remaining_bytes = _MAX_RESPONSE_BYTES
    deadline = time.monotonic() + timeout_seconds

    for _ in range(_MAX_MODEL_PAGES):
        remaining_timeout = deadline - time.monotonic()
        if remaining_timeout <= 0:
            raise TimeoutError("Provider model discovery timed out")
        query = urlencode({"after_id": cursor}) if cursor else ""
        request = urllib.request.Request(
            f"{models_url}?{query}" if query else models_url,
            headers=headers,
            method="GET",
        )
        with opener.open(request, timeout=remaining_timeout) as response:
            raw = response.read(remaining_bytes + 1)
        if len(raw) > remaining_bytes:
            raise ValueError("Provider model response is too large")
        remaining_bytes -= len(raw)
        payload = json.loads(raw.decode("utf-8"))
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        models.update(
            entry.get("id", "").strip()
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        )

        has_more = (
            normalized == "anthropic"
            and isinstance(payload, dict)
            and payload.get("has_more") is True
        )
        if not has_more:
            break
        next_cursor = payload.get("last_id", "")
        if (
            not isinstance(next_cursor, str)
            or not _MODEL_ID_RE.fullmatch(next_cursor)
            or next_cursor in seen_cursors
        ):
            raise ValueError("Provider returned an invalid model pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise ValueError("Provider model pagination exceeded the safe page limit")

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
