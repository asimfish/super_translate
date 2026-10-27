"""Catalog protocol, pagination, credential isolation and failure regressions."""

from __future__ import annotations

import asyncio
import io
import json
import time
import urllib.error
from unittest.mock import AsyncMock

import pytest

from app.core.provider_credentials import PROVIDER_SPECS
from app.services import provider_model_catalog as catalog
from pdf_zh_translator.translators import VendorTranslator


@pytest.fixture(autouse=True)
def empty_catalog_cache():
    catalog._cache.clear()
    yield
    catalog._cache.clear()


def install_responses(monkeypatch, payloads):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert 0 < timeout <= catalog.MODEL_DISCOVERY_TIMEOUT_SECONDS
            return io.BytesIO(json.dumps(payloads.pop(0)).encode())

    monkeypatch.setattr(catalog.urllib.request, "build_opener", lambda *args: Opener())
    return requests


@pytest.mark.parametrize("provider", PROVIDER_SPECS)
def test_offline_catalog_includes_defaults_and_only_text_models(provider):
    models = catalog.curated_provider_models(provider)
    assert PROVIDER_SPECS[provider].default_model in models
    assert len(models) == len(set(models))
    assert all(catalog._is_translation_model(provider, model) for model in models)


def test_gemini_native_discovery_paginates_and_filters_audio_image(monkeypatch):
    requests = install_responses(
        monkeypatch,
        [
            {
                "models": [
                    {
                        "name": "models/gemini-future-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-future-tts",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ],
                "nextPageToken": "page/2",
            },
            {
                "models": [
                    {
                        "name": "models/gemini-future-pro",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        ],
    )
    models = catalog.fetch_provider_models(
        "gemini", api_key="test-key-not-real", base_url=PROVIDER_SPECS["gemini"].base_url
    )
    assert models == ("gemini-future-flash", "gemini-future-pro")
    assert "pageToken=page%2F2" in requests[1].full_url
    assert requests[0].get_header("X-goog-api-key") == "test-key-not-real"
    assert "test-key" not in requests[0].full_url


def test_qwen_native_discovery_uses_output_models_and_total(monkeypatch):
    requests = install_responses(
        monkeypatch,
        [
            {"output": {"total": 101, "models": [{"model": "qwen-future"}]}},
            {"output": {"total": 101, "models": [{"model": "qwq-plus"}]}},
        ],
    )
    assert catalog.fetch_provider_models(
        "qwen", api_key="test-key-not-real", base_url=PROVIDER_SPECS["qwen"].base_url
    ) == ("qwen-future", "qwq-plus")
    assert "page_no=2" in requests[1].full_url
    assert requests[0].full_url.startswith("https://dashscope.aliyuncs.com/api/v1/models?")


def test_anthropic_new_versions_and_retired_models(monkeypatch):
    requests = install_responses(
        monkeypatch,
        [
            {"data": [{"id": "claude-future"}], "has_more": True, "last_id": "claude-future"},
            {"data": [{"id": "claude-retired", "lifecycle": "retired"}], "has_more": False},
        ],
    )
    assert catalog.fetch_provider_models(
        "anthropic", api_key="test-key-not-real", base_url=PROVIDER_SPECS["anthropic"].base_url
    ) == ("claude-future",)
    assert "after_id=claude-future" in requests[1].full_url
    assert requests[0].get_header("Anthropic-version") == "2023-06-01"


@pytest.mark.parametrize(
    "provider,model",
    [
        ("deepseek", "deepseek-flash"),
        ("kimi", "moonshot-future"),
        ("openai", "gpt-6.1-sol"),
        ("glm", "glm-5.3-flash"),
        ("doubao", "ep-20261010-test"),
    ],
)
def test_openai_shaped_discovery_accepts_future_versions(monkeypatch, provider, model):
    requests = install_responses(monkeypatch, [{"data": [{"id": model}, {"id": "embedding-3"}]}])
    assert catalog.fetch_provider_models(
        provider, api_key="test-key-not-real", base_url=PROVIDER_SPECS[provider].base_url
    ) == (model,)
    assert requests[0].get_header("Authorization") == "Bearer test-key-not-real"


def test_discovery_refuses_changed_destination_and_redirects():
    with pytest.raises(ValueError, match="Non-official"):
        catalog.fetch_provider_models(
            "openai", api_key="test-key-not-real", base_url="https://attacker.example/v1"
        )
    handler = catalog._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "", {}, "https://attacker.example") is None


def test_repeated_pagination_cursor_is_bounded(monkeypatch):
    install_responses(
        monkeypatch,
        [
            {"data": [], "has_more": True, "last_id": "repeat"},
            {"data": [], "has_more": True, "last_id": "repeat"},
        ],
    )
    with pytest.raises(ValueError, match="pagination cursor"):
        catalog.fetch_provider_models(
            "anthropic", api_key="test-key-not-real", base_url=PROVIDER_SPECS["anthropic"].base_url
        )


@pytest.mark.asyncio
async def test_cache_separates_accounts_keys_and_coalesces_calls(monkeypatch):
    calls = []

    def fetch(provider, *, api_key, base_url):
        calls.append(api_key)
        time.sleep(0.03)
        return ("gpt-account-" + api_key[-1],)

    monkeypatch.setattr(catalog, "fetch_provider_models", fetch)
    first = await asyncio.gather(
        *(
            catalog.get_provider_model_catalog("openai", access_scope="alice", api_key="fake-A")
            for _ in range(8)
        )
    )
    assert calls == ["fake-A"]
    assert all(item.available_models == ("gpt-account-A",) for item in first)
    await catalog.get_provider_model_catalog("openai", access_scope="bob", api_key="fake-A")
    changed = await catalog.get_provider_model_catalog(
        "openai", access_scope="alice", api_key="fake-B"
    )
    assert calls == ["fake-A", "fake-A", "fake-B"]
    assert changed.available_models == ("gpt-account-B",)
    assert all("fake-" not in key for key in catalog._cache)


@pytest.mark.asyncio
async def test_failure_retains_catalog_and_backoff_hides_secret(monkeypatch):
    monkeypatch.setattr(catalog, "fetch_provider_models", lambda *args, **kw: ("gpt-good",))
    first = await catalog.get_provider_model_catalog(
        "openai", access_scope="alice", api_key="fake-A"
    )
    failures = []

    def fail(*args, **kwargs):
        failures.append(True)
        raise urllib.error.HTTPError("https://api.openai.com", 401, "fake-secret-error", {}, None)

    monkeypatch.setattr(catalog, "fetch_provider_models", fail)
    stale = await catalog.get_provider_model_catalog(
        "openai", access_scope="alice", api_key="fake-A", force_refresh=True
    )
    repeated = await catalog.get_provider_model_catalog(
        "openai", access_scope="alice", api_key="fake-A", force_refresh=True
    )
    assert failures == [True]
    assert stale.source == repeated.source == "stale"
    assert stale.refreshed_at == first.refreshed_at
    assert stale.available_models == ("gpt-good",)
    assert "fake-secret-error" not in stale.warning


@pytest.mark.asyncio
async def test_catalog_api_never_reads_another_users_server_key(monkeypatch):
    from app.api import provider_credentials as api

    def server(provider):
        pytest.fail("Other accounts may not inherit server credentials")

    monkeypatch.setattr(api, "server_provider_credential", server)
    monkeypatch.setattr(api, "load_provider_credential", AsyncMock(return_value=None))
    responses = await api.list_provider_models(AsyncMock(), "bob")
    assert {item.provider for item in responses} == set(PROVIDER_SPECS)
    assert all(item.source == "curated" for item in responses)
    assert all(
        option.availability == "reference" for item in responses for option in item.model_options
    )
    assert "api_key" not in json.dumps([item.model_dump(mode="json") for item in responses])


@pytest.mark.parametrize(
    "provider,model",
    [
        ("gemini", "gemini-3.8-flash"),
        ("qwen", "qwen3.8-flash"),
        ("doubao", "ep-20261010-test"),
        ("openai", "gpt-6.1-sol"),
    ],
)
def test_new_providers_translate_and_plain_fallback_use_correct_protocol(
    monkeypatch, provider, model
):
    sent = []

    def post(self, url, payload):
        sent.append((url, payload))
        content = "你好" if len(sent) == 2 else '["你好"]'
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(VendorTranslator, "_post_json", post)
    vendor = VendorTranslator(
        api_url=PROVIDER_SPECS[provider].base_url,
        api_key="test-key-not-real",
        mode="openai-compatible",
        model=model,
        progress=False,
    )
    assert vendor.translate_batch(["Hello"]) == ["你好"]
    assert vendor._translate_openai_plain("Hello") == "你好"
    assert all(url.endswith("/chat/completions") for url, _ in sent)
    assert all(payload["model"] == model for _, payload in sent)
    for _, payload in sent:
        if provider == "qwen":
            assert payload["enable_thinking"] is False
        if provider == "doubao":
            assert payload["thinking"] == {"type": "disabled"}
        if provider == "openai":
            assert "temperature" not in payload and "max_tokens" not in payload
            assert payload["max_completion_tokens"] == vendor.max_output_tokens


@pytest.mark.parametrize("provider", ["gemini", "qwen", "doubao"])
def test_new_backends_cannot_silently_fall_back_to_google(provider):
    from app.api.papers import _resolve_backend_config
    from app.core.provider_credentials import ResolvedProviderCredential
    from app.services.translator import QualityPreset, TranslationConfig, _use_native_engine

    spec = PROVIDER_SPECS[provider]
    credential = ResolvedProviderCredential(provider, "fake-key", spec.base_url, spec.default_model)
    config = _resolve_backend_config(
        provider, QualityPreset.BALANCED, provider_credential=credential, allow_server_default=False
    )
    assert config.backend == provider and config.api_key == "fake-key"
    assert _use_native_engine(TranslationConfig(backend=provider)) is True


@pytest.mark.parametrize("provider", PROVIDER_SPECS)
def test_eight_provider_adapters_send_real_http_auth_and_parse_chinese(monkeypatch, provider):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from urllib.parse import urlsplit

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, dict(self.headers), body))
            result = (
                {"content": [{"type": "text", "text": '["你好世界"]'}]}
                if provider == "anthropic"
                else {"choices": [{"message": {"content": '["你好世界"]'}}]}
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_open = catalog.urllib.request.urlopen

    def local_transport(request, timeout):
        parsed = urlsplit(request.full_url)
        local_request = catalog.urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{parsed.path}",
            data=request.data,
            headers=dict(request.headers),
            method=request.method,
        )
        return original_open(local_request, timeout=timeout)

    monkeypatch.setattr(catalog.urllib.request, "urlopen", local_transport)
    spec = PROVIDER_SPECS[provider]
    vendor = VendorTranslator(
        api_url=spec.base_url,
        api_key="fake-wire-key",
        mode=spec.protocol,
        model=spec.default_model,
        progress=False,
        retries=0,
    )
    try:
        assert vendor.translate_batch(["Hello world"]) == ["你好世界"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    path, headers, body = received[0]
    assert body["model"] == spec.default_model
    if provider == "anthropic":
        assert path.endswith("/messages") and headers["X-Api-Key"] == "fake-wire-key"
        assert headers["Anthropic-Version"] == "2023-06-01"
    else:
        assert path.endswith("/chat/completions")
        assert headers["Authorization"] == "Bearer fake-wire-key"
