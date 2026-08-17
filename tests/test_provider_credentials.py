"""User-scoped provider credential and multi-vendor translation regressions."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import SecretStr

from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def credential_key(monkeypatch):
    key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setattr(settings, "credential_encryption_key", SecretStr(key))
    return key


def test_provider_catalog_contains_requested_vendors():
    from app.core.provider_credentials import PROVIDER_SPECS

    assert set(PROVIDER_SPECS) == {"deepseek", "kimi", "openai", "anthropic", "glm"}
    assert PROVIDER_SPECS["glm"].base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert PROVIDER_SPECS["anthropic"].base_url == "https://api.anthropic.com/v1"


def test_api_key_ciphertext_is_bound_to_scope_and_provider(credential_key):
    from app.core.provider_credentials import (
        CredentialDecryptionError,
        decrypt_api_key,
        encrypt_api_key,
    )

    secret = "sk-user-secret-value"
    ciphertext = encrypt_api_key(secret, "alice", "deepseek")

    assert secret not in ciphertext
    assert decrypt_api_key(ciphertext, "alice", "deepseek") == secret
    with pytest.raises(CredentialDecryptionError):
        decrypt_api_key(ciphertext, "bob", "deepseek")
    with pytest.raises(CredentialDecryptionError):
        decrypt_api_key(ciphertext, "alice", "openai")


@pytest.mark.asyncio
async def test_credential_upsert_never_returns_or_stores_plaintext(credential_key):
    from app.api.provider_credentials import ProviderCredentialUpdate, save_provider_credential

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.commit = AsyncMock()

    response = await save_provider_credential(
        "deepseek",
        ProviderCredentialUpdate(api_key="sk-private-deepseek", model="deepseek-v4-pro"),
        db,
        "alice",
    )

    stored = db.add.call_args.args[0]
    assert stored.access_scope == "alice"
    assert stored.provider == "deepseek"
    assert "sk-private-deepseek" not in stored.encrypted_api_key
    assert response.configured is True
    assert response.key_hint.endswith("seek")
    assert "api_key" not in response.model_dump()


@pytest.mark.asyncio
async def test_credential_lookup_filters_by_access_scope(credential_key):
    from app.core.provider_credentials import load_provider_credential

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    assert await load_provider_credential(db, "team-a", "glm") is None
    statement = db.scalar.await_args.args[0]
    sql = str(statement)
    assert "provider_credentials.access_scope" in sql
    assert "provider_credentials.provider" in sql


@pytest.mark.asyncio
async def test_real_database_credentials_are_isolated_by_user(credential_key):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import Base
    from app.core.provider_credentials import encrypt_api_key, load_provider_credential
    from app.models.provider_credential import ProviderCredential

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as db:
        db.add(
            ProviderCredential(
                access_scope="alice",
                provider="openai",
                encrypted_api_key=encrypt_api_key("sk-alice-secret", "alice", "openai"),
                key_hint="••••cret",
                model="gpt-4o-mini",
            )
        )
        await db.commit()
        alice = await load_provider_credential(db, "alice", "openai")
        bob = await load_provider_credential(db, "bob", "openai")

    await engine.dispose()
    assert alice is not None
    assert alice.api_key == "sk-alice-secret"
    assert bob is None


def test_anthropic_vendor_uses_messages_protocol():
    from pdf_zh_translator.translators import VendorTranslator

    translator = VendorTranslator(
        api_url="https://api.anthropic.com/v1",
        api_key="anthropic-key",
        mode="anthropic",
        model="claude-sonnet-5",
        progress=False,
    )
    response = {"content": [{"type": "text", "text": '["\u8bd1\u6587"]'}]}

    with patch.object(translator, "_post_json", return_value=response) as post_json:
        assert translator.translate_batch(["Source"]) == ["\u8bd1\u6587"]

    url, payload = post_json.call_args.args
    assert url == "https://api.anthropic.com/v1/messages"
    assert payload["model"] == "claude-sonnet-5"
    assert isinstance(payload["system"], str)
    assert payload["messages"] == [{"role": "user", "content": '["Source"]'}]
    assert "temperature" not in payload


def test_anthropic_http_auth_uses_vendor_headers_not_bearer():
    from pdf_zh_translator.translators import VendorTranslator

    translator = VendorTranslator(
        api_url="https://api.anthropic.com/v1",
        api_key="anthropic-secret",
        mode="anthropic",
        retries=0,
        progress=False,
    )
    response = MagicMock()
    response.read.return_value = b'{"content": [{"type": "text", "text": "ok"}]}'
    response.__enter__.return_value = response

    with (
        patch("urllib.request.Request") as request,
        patch("urllib.request.urlopen", return_value=response),
    ):
        translator._post_json("https://api.anthropic.com/v1/messages", {"model": "x"})

    headers = request.call_args.kwargs["headers"]
    assert headers["x-api-key"] == "anthropic-secret"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers


def test_glm_uses_native_openai_compatible_configuration(tmp_path):
    from app.services.translator import TranslationConfig, _translate_sync_native

    input_path = tmp_path / "paper.pdf"
    input_path.write_bytes(b"%PDF-1.4 fake")
    output_dir = tmp_path / "output"

    def write_output(**kwargs):
        kwargs["output_pdf"].write_bytes(b"%PDF-1.4 translated")
        return MagicMock(warnings=[])

    with (
        patch("pdf_zh_translator.pdf_layout.translate_pdf", side_effect=write_output),
        patch("pdf_zh_translator.pdf_layout.create_dual_pdf"),
        patch("pdf_zh_translator.pdf_layout.verify_translation", return_value=[]),
        patch("pdf_zh_translator.translators.CachedTranslator"),
        patch("pdf_zh_translator.translators.VendorTranslator") as vendor,
    ):
        result = _translate_sync_native(
            input_path,
            output_dir,
            TranslationConfig(
                backend="glm",
                api_key="glm-key",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-5.2",
            ),
        )

    assert result.success is True
    assert vendor.call_args.kwargs["mode"] == "openai-compatible"
    assert vendor.call_args.kwargs["api_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert vendor.call_args.kwargs["model"] == "glm-5.2"


def test_worker_spec_never_persists_api_key(tmp_path):
    from app.api.papers import _run_translate_in_worker
    from app.services.translator import TranslationConfig

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = {"mono_path": str(tmp_path / "m.pdf"), "dual_path": None, "error": None}
    captured = {}

    class SecretPipe:
        def write(self, data):
            captured["stdin"] = data

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_exec(*args, **kwargs):
        captured["spec"] = json.loads((output_dir / ".worker_spec.json").read_text())
        proc = MagicMock()
        proc.returncode = 0
        proc.stdin = SecretPipe()
        proc.wait = AsyncMock(return_value=0)
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        (output_dir / ".worker_result.json").write_text(json.dumps(result))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        translated = asyncio.run(
            _run_translate_in_worker(
                tmp_path / "in.pdf",
                output_dir,
                TranslationConfig(backend="deepseek", api_key="sk-worker-secret"),
                lambda pct: None,
                paper_id="paper123",
            )
        )

    assert translated.success is True
    assert "api_key" not in captured["spec"]["config"]
    assert b"sk-worker-secret" in captured["stdin"]
    assert "sk-worker-secret" not in (output_dir / ".worker_spec.json").read_text()


def test_ui_exposes_user_provider_settings_without_key_echo():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert 'id="btn-provider-settings"' in html
    assert 'id="provider-settings-modal"' in html
    for provider in ("deepseek", "kimi", "openai", "anthropic", "glm"):
        assert f'data-provider="{provider}"' in html
    assert html.count('<select class="provider-model"') == 5
    assert '<input class="provider-model"' not in html
    assert "async listProviderCredentials()" in js
    assert "async listProviderModels()" in js
    assert "async refreshProviderModels(provider)" in js
    assert "async saveProviderCredential(provider, payload)" in js
    assert "renderProviderModelCatalogs" in js
    assert ".api_key" not in js


def test_curated_model_catalog_always_contains_provider_defaults():
    from app.core.provider_credentials import PROVIDER_SPECS
    from app.services.provider_model_catalog import curated_provider_models

    for provider, spec in PROVIDER_SPECS.items():
        assert spec.default_model in curated_provider_models(provider)


def test_provider_model_discovery_uses_vendor_auth_and_filters_non_text_models():
    from app.services.provider_model_catalog import fetch_provider_models

    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "data": [
                {"id": "claude-sonnet-5"},
                {"id": "claude-opus-5"},
                {"id": "text-embedding-irrelevant"},
            ]
        }
    ).encode()
    response.__enter__.return_value = response

    opener = MagicMock()
    opener.open.return_value = response
    with patch("urllib.request.build_opener", return_value=opener):
        models = fetch_provider_models(
            "anthropic",
            api_key="anthropic-private-key",
            base_url="https://api.anthropic.com/v1",
            timeout_seconds=3,
        )

    request = opener.open.call_args.args[0]
    assert request.full_url == "https://api.anthropic.com/v1/models"
    assert request.headers["X-api-key"] == "anthropic-private-key"
    assert request.headers["Anthropic-version"] == "2023-06-01"
    assert request.headers.get("Authorization") is None
    assert models == ("claude-opus-5", "claude-sonnet-5")


def test_provider_model_discovery_rejects_redirects_before_resending_credentials():
    from app.services.provider_model_catalog import fetch_provider_models

    redirected_headers = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/models":
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{self.server.server_port}/credential-sink",
                )
                self.end_headers()
                return
            redirected_headers.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": [{"id": "gpt-4o-mini"}]}')

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            fetch_provider_models(
                "openai",
                api_key="sk-must-not-follow-redirect",
                base_url=f"http://127.0.0.1:{server.server_port}",
                timeout_seconds=3,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert exc_info.value.code == 302
    assert redirected_headers == []


@pytest.mark.asyncio
async def test_model_catalog_api_never_returns_provider_secret():
    from app.api.provider_credentials import list_provider_models
    from app.core.provider_credentials import ResolvedProviderCredential

    credential = ResolvedProviderCredential(
        provider="deepseek",
        api_key="sk-private-model-discovery",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
    )
    db = AsyncMock()
    with (
        patch(
            "app.api.provider_credentials.load_provider_credential",
            side_effect=lambda _db, _scope, provider: (
                credential if provider == "deepseek" else None
            ),
        ),
        patch(
            "app.api.provider_credentials.get_provider_model_catalog",
            new=AsyncMock(
                side_effect=lambda provider, **_kwargs: MagicMock(
                    provider=provider,
                    models=(
                        "deepseek-v4-pro",
                        "deepseek-v4-flash",
                    )
                    if provider == "deepseek"
                    else (f"{provider}-default",),
                    source="provider" if provider == "deepseek" else "curated",
                    refreshed_at=None,
                    warning="",
                )
            ),
        ),
    ):
        catalogs = await list_provider_models(db, "alice")

    serialized = json.dumps(
        [catalog.model_dump(mode="json") for catalog in catalogs],
        ensure_ascii=False,
    )
    assert "sk-private-model-discovery" not in serialized
    assert catalogs[0].selected_model == "deepseek-v4-pro"
    assert catalogs[0].models == ["deepseek-v4-pro", "deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_model_catalog_cache_is_scoped_and_force_refreshable():
    from app.services.provider_model_catalog import get_provider_model_catalog

    discovered = ("deepseek-account-model",)
    with patch(
        "app.services.provider_model_catalog.fetch_provider_models",
        return_value=discovered,
    ) as fetch:
        first = await get_provider_model_catalog(
            "deepseek",
            access_scope="cache-user-a",
            api_key="sk-cache-user-a",
        )
        cached = await get_provider_model_catalog(
            "deepseek",
            access_scope="cache-user-a",
            api_key="sk-cache-user-a",
        )
        other_user = await get_provider_model_catalog(
            "deepseek",
            access_scope="cache-user-b",
            api_key="sk-cache-user-b",
        )
        refreshed = await get_provider_model_catalog(
            "deepseek",
            access_scope="cache-user-a",
            api_key="sk-cache-user-a",
            force_refresh=True,
        )

    assert first.source == "provider"
    assert cached.source == "cache"
    assert other_user.source == "provider"
    assert refreshed.source == "provider"
    assert fetch.call_count == 3


@pytest.mark.asyncio
async def test_model_catalog_refresh_failure_returns_safe_curated_fallback():
    from app.services.provider_model_catalog import get_provider_model_catalog

    with patch(
        "app.services.provider_model_catalog.fetch_provider_models",
        side_effect=TimeoutError("upstream timed out with a secret-bearing URL"),
    ):
        catalog = await get_provider_model_catalog(
            "openai",
            access_scope="fallback-user",
            api_key="sk-fallback-private",
            force_refresh=True,
        )

    assert catalog.source == "curated"
    assert catalog.warning == "无法更新模型列表，已使用内置列表"
    assert "gpt-4o-mini" in catalog.models
    assert "secret" not in catalog.warning


@pytest.mark.asyncio
async def test_remote_user_without_personal_key_is_rejected_before_queueing():
    from app.api.papers import start_translation

    db = AsyncMock()
    paper_result = MagicMock()
    paper_result.scalar_one_or_none.return_value = MagicMock()
    db.execute = AsyncMock(return_value=paper_result)
    db.scalar = AsyncMock(return_value=None)

    with patch("app.api.papers._schedule_background_task") as schedule:
        with pytest.raises(HTTPException) as exc_info:
            await start_translation(
                "abcd12345678",
                BackgroundTasks(),
                db,
                "alice",
                backend="openai",
            )

    assert exc_info.value.status_code == 400
    assert "API 设置" in exc_info.value.detail
    schedule.assert_not_called()
