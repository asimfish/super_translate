"""Official model discovery with credential-isolated caching and safe fallbacks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import Lock
from urllib.parse import urlencode

from app.core.provider_credentials import PROVIDER_SPECS, validate_provider

MODEL_CATALOG_TTL_SECONDS = 5 * 60
MODEL_DISCOVERY_TIMEOUT_SECONDS = 12
MODEL_CATALOG_VERIFIED_ON = date(2026, 10, 10)
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_MODEL_PAGES = 20
_MAX_CACHE_ENTRIES = 512
_FAILURE_BACKOFF_SECONDS = 60
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")

# Official documentation references and the verification procedure live in
# docs/MODEL_CATALOG.md. These are reference choices, not account entitlements.
_CURATED_MODELS: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash"),
    "kimi": (
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k2.6",
        "kimi-k2.5",
        "kimi-k2-thinking",
        "kimi-k2-thinking-turbo",
        "kimi-k2-0905-preview",
        "kimi-k2-turbo-preview",
        "moonshot-v1-8k",
        "moonshot-v1-32k",
        "moonshot-v1-128k",
    ),
    "openai": (
        "gpt-6.1-sol",
        "gpt-6-astra",
        "gpt-6-luna",
        "gpt-5.6",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
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
        "o3",
        "o3-mini",
        "o4-mini",
    ),
    "anthropic": (
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ),
    "glm": (
        "glm-5.3",
        "glm-5.3-flash",
        "glm-5.3-flashx",
        "glm-5.2",
        "glm-5.1",
        "glm-5-turbo",
        "glm-5",
        "glm-4.7",
        "glm-4.7-flash",
        "glm-4.7-flashx",
        "glm-4.6",
        "glm-4.6v",
        "glm-4.6v-flash",
        "glm-4.5-air",
        "glm-4.5-airx",
        "glm-4.5-flash",
        "glm-4-flash-250414",
        "glm-4-flashx-250414",
    ),
    "gemini": (
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ),
    "qwen": (
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3.7-plus",
        "qwen3.5-plus",
        "qwen3.5-flash",
        "qwen3-max",
        "qwen3-max-preview",
        "qwen-plus",
        "qwen-plus-latest",
        "qwen-turbo",
        "qwen-turbo-latest",
        "qwen-max",
        "qwen-max-latest",
        "qwen-long",
        "qwq-plus",
    ),
    "doubao": (
        "doubao-seed-2-1-pro-260915",
        "doubao-seed-2-1-pro-260628",
        "doubao-seed-2-0-pro-260215",
        "doubao-seed-2-0-lite-260215",
        "doubao-seed-2-0-mini-260215",
        "doubao-seed-1-8-251228",
        "doubao-seed-1-6-250615",
        "doubao-seed-1-6-flash-250615",
    ),
}


@dataclass(frozen=True)
class ProviderModelCatalog:
    provider: str
    models: tuple[str, ...]
    source: str
    refreshed_at: datetime | None = None
    warning: str = ""
    available_models: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CacheEntry:
    models: tuple[str, ...]
    refreshed_at: datetime | None
    expires_at: float
    checked_at: float
    warning: str = ""


_cache: OrderedDict[str, _CacheEntry] = OrderedDict()
_cache_lock = Lock()
# A bounded lock pool coalesces refreshes without keeping raw API keys or an
# unbounded table of locks. Different accounts never share catalog values.
_refresh_locks = tuple(Lock() for _ in range(64))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def curated_provider_models(provider: str) -> tuple[str, ...]:
    normalized = validate_provider(provider)
    return tuple(
        dict.fromkeys((*_CURATED_MODELS[normalized], PROVIDER_SPECS[normalized].default_model))
    )


def provider_model_guidance(provider: str, model: str) -> tuple[str, str]:
    models = curated_provider_models(provider)
    if model in models[:3]:
        return ("latest", "近期模型")
    if any(part in model for part in ("flash", "mini", "nano", "lite", "turbo", "haiku")):
        return ("economy", "速度 / 成本优先")
    if any(part in model for part in ("pro", "max", "opus", "astra", "fable", "thinking")):
        return ("quality", "复杂文本 / 推理")
    return ("balanced", "通用文本")


def _is_translation_model(provider: str, model: str) -> bool:
    if not _MODEL_ID_RE.fullmatch(model):
        return False
    lowered = model.lower()
    excluded = (
        "embedding",
        "rerank",
        "ocr",
        "asr",
        "tts",
        "image",
        "audio",
        "realtime",
        "transcribe",
        "whisper",
        "video",
        "live",
        "omni",
        "nano-banana",
        "seedance",
        "seedream",
        "deep-research",
    )
    if any(part in lowered for part in excluded):
        return False
    prefixes = {
        "deepseek": ("deepseek-",),
        "kimi": ("kimi-", "moonshot-"),
        "openai": ("gpt-", "o1", "o3", "o4", "chatgpt-"),
        "anthropic": ("claude-",),
        "glm": ("glm-",),
        "gemini": ("gemini-",),
        "qwen": ("qwen", "qwq"),
        "doubao": ("doubao-", "ep-"),
    }
    if provider == "openai" and any(part in lowered for part in ("codex", "-pro", "instruct")):
        # Responses-only models cannot use our chat-completions adapter.
        return False
    return lowered.startswith(prefixes[provider])


def _active_text_entry(provider: str, entry: dict, model: str) -> bool:
    if not _is_translation_model(provider, model):
        return False
    if entry.get("lifecycle") == "retired":
        return False
    shutdown = entry.get("shutdown_date")
    if isinstance(shutdown, str) and shutdown[:10] <= date.today().isoformat():
        return False
    if provider == "gemini" and "generateContent" not in entry.get(
        "supportedGenerationMethods", []
    ):
        return False
    modalities = entry.get("inference_metadata", {}).get("response_modality")
    return not modalities or "Text" in modalities


def fetch_provider_models(
    provider: str,
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: float = MODEL_DISCOVERY_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    normalized = validate_provider(provider)
    # No request field controls a destination. Custom administrator proxies are
    # not probed with credentials against a different service.
    if base_url.rstrip("/") != PROVIDER_SPECS[normalized].base_url.rstrip("/"):
        raise ValueError("Non-official discovery endpoint")
    headers = {"Accept": "application/json", "User-Agent": "SuperTranslate/0.3 model-catalog"}
    if normalized == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    elif normalized == "gemini":
        headers["x-goog-api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    models_url = f"{base_url.rstrip('/')}/models"
    if normalized == "gemini":
        models_url = "https://generativelanguage.googleapis.com/v1beta/models"
    elif normalized == "qwen":
        models_url = "https://dashscope.aliyuncs.com/api/v1/models"
    opener = urllib.request.build_opener(_NoRedirectHandler())
    models: dict[str, None] = {}
    cursor = ""
    seen_cursors: set[str] = set()
    byte_budget = _MAX_RESPONSE_BYTES
    deadline = time.monotonic() + timeout_seconds
    for page in range(1, _MAX_MODEL_PAGES + 1):
        query: dict[str, str | int] = {}
        if normalized == "anthropic":
            query = {"limit": 100}
            if cursor:
                query["after_id"] = cursor
        elif normalized == "gemini":
            query = {"pageSize": 100}
            if cursor:
                query["pageToken"] = cursor
        elif normalized == "qwen":
            query = {"providers": "qwen", "capabilities": "TG", "page_no": page, "page_size": 100}
        url = models_url + ("?" + urlencode(query) if query else "")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Model discovery deadline exceeded")
        request = urllib.request.Request(url, headers=headers, method="GET")
        with opener.open(request, timeout=remaining) as response:
            body = response.read(byte_budget + 1)
        byte_budget -= len(body)
        if byte_budget < 0:
            raise ValueError("Model response exceeds size limit")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Malformed model response")
        entries = payload.get("data", [])
        if normalized == "gemini":
            entries = payload.get("models", [])
        elif normalized == "qwen":
            entries = payload.get("output", {}).get("models", [])
        if not isinstance(entries, list):
            raise ValueError("Malformed model entries")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model = entry.get("id") or entry.get("model") or entry.get("name", "")
            if normalized == "gemini" and isinstance(model, str):
                model = model.removeprefix("models/")
            if isinstance(model, str) and _active_text_entry(normalized, entry, model):
                models[model] = None
        if normalized == "anthropic" and payload.get("has_more"):
            cursor = payload.get("last_id", "")
        elif normalized == "gemini" and payload.get("nextPageToken"):
            cursor = payload["nextPageToken"]
        elif normalized == "qwen":
            total = payload.get("output", {}).get("total", 0)
            if not isinstance(total, int) or total < 0:
                raise ValueError("Malformed model total")
            if page * 100 < total and entries:
                continue
            break
        else:
            break
        if (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > 2048
            or cursor in seen_cursors
        ):
            raise ValueError("Invalid model pagination cursor")
        seen_cursors.add(cursor)
    else:
        raise ValueError("Model pagination exceeds page limit")
    return tuple(models)


def _catalog_from_entry(provider: str, entry: _CacheEntry, *, fresh: bool) -> ProviderModelCatalog:
    source = "stale" if entry.warning and entry.models else ("provider" if fresh else "cache")
    if not entry.models:
        source = "curated"
    return ProviderModelCatalog(
        provider=provider,
        models=tuple(dict.fromkeys((*entry.models, *curated_provider_models(provider)))),
        available_models=entry.models,
        source=source,
        refreshed_at=entry.refreshed_at,
        warning=entry.warning,
    )


def _refresh_catalog_sync(
    provider: str,
    cache_key: str,
    api_key: str,
    base_url: str,
    force_refresh: bool,
    requested_at: float,
) -> ProviderModelCatalog:
    lock = _refresh_locks[int(cache_key[:8], 16) % len(_refresh_locks)]
    with lock:
        now = time.monotonic()
        with _cache_lock:
            cached = _cache.get(cache_key)
        if cached and (
            cached.checked_at >= requested_at
            or (cached.expires_at > now and (not force_refresh or cached.warning))
        ):
            return _catalog_from_entry(provider, cached, fresh=False)
        warning = ""
        try:
            discovered = fetch_provider_models(provider, api_key=api_key, base_url=base_url)
            if not discovered:
                raise ValueError("No compatible models returned")
            refreshed_at = datetime.now(timezone.utc)
        except (OSError, TimeoutError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            discovered = cached.models if cached else ()
            refreshed_at = cached.refreshed_at if cached else None
            if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403}:
                reason = "密钥无效或没有查询模型权限"
            elif isinstance(exc, urllib.error.HTTPError) and exc.code in {404, 405}:
                reason = "供应商未开放此模型查询接口，可手动填写模型或 Endpoint ID"
            elif isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                reason = "供应商限制了查询频率，将稍后重试"
            else:
                reason = "模型目录暂时无法更新，将自动重试"
            warning = reason + ("；保留上次目录" if discovered else "；当前为内置参考")
        entry = _CacheEntry(
            models=discovered,
            refreshed_at=refreshed_at,
            checked_at=time.monotonic(),
            expires_at=time.monotonic()
            + (_FAILURE_BACKOFF_SECONDS if warning else MODEL_CATALOG_TTL_SECONDS),
            warning=warning,
        )
        with _cache_lock:
            _cache[cache_key] = entry
            _cache.move_to_end(cache_key)
            while len(_cache) > _MAX_CACHE_ENTRIES:
                _cache.popitem(last=False)
        return _catalog_from_entry(provider, entry, fresh=True)


async def get_provider_model_catalog(
    provider: str,
    *,
    access_scope: str,
    api_key: str | None,
    base_url: str | None = None,
    force_refresh: bool = False,
) -> ProviderModelCatalog:
    normalized = validate_provider(provider)
    if not api_key:
        return ProviderModelCatalog(normalized, curated_provider_models(normalized), "curated")
    endpoint = base_url or PROVIDER_SPECS[normalized].base_url
    cache_key = hashlib.sha256(
        json.dumps([access_scope, normalized, endpoint, api_key], ensure_ascii=True).encode()
    ).hexdigest()
    return await asyncio.to_thread(
        _refresh_catalog_sync,
        normalized,
        cache_key,
        api_key,
        endpoint,
        force_refresh,
        time.monotonic(),
    )
