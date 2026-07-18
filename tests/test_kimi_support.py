"""Focused regression coverage for the Kimi K3 translation backend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import BackgroundTasks
from pydantic import SecretStr

from app.api.papers import _resolve_backend_config, start_translation
from app.services.translator import (
    QualityPreset,
    TranslationConfig,
    _resolve_service,
    _translate_sync_native,
    _use_native_engine,
)
from pdf_zh_translator.translators import VendorTranslator

ROOT = Path(__file__).resolve().parents[1]


def test_kimi_uses_native_engine_and_pdf2zh_openai_adapter():
    config = TranslationConfig(backend="kimi", api_key="moonshot-key")

    with patch("app.core.config.settings.translation_engine", "native"):
        assert _use_native_engine(config) is True

    assert _resolve_service(config, "google") == "openai"


def test_kimi_uses_constraint_aware_native_adapter_when_pdf2zh_is_selected():
    config = TranslationConfig(backend="kimi", api_key="moonshot-key")

    with patch("app.core.config.settings.translation_engine", "pdf2zh"):
        assert _use_native_engine(config) is True


def test_resolve_kimi_backend_config_uses_moonshot_settings():
    with patch("app.api.papers.settings") as mock_settings:
        mock_settings.moonshot_api_key = SecretStr("moonshot-key")
        mock_settings.moonshot_base_url = "https://api.moonshot.cn/v1"
        mock_settings.kimi_model = "kimi-k3"

        config = _resolve_backend_config("kimi", QualityPreset.BALANCED)

    assert config.backend == "kimi"
    assert config.api_key == "moonshot-key"
    assert config.base_url == "https://api.moonshot.cn/v1"
    assert config.model == "kimi-k3"


def test_translation_endpoint_accepts_and_schedules_kimi():
    db = AsyncMock()
    db.execute.return_value = MagicMock(rowcount=1)
    db.add = MagicMock()
    db.commit = AsyncMock()

    with patch("app.api.papers._schedule_background_task") as schedule:
        response = asyncio.run(
            start_translation(
                "abcd12345678",
                BackgroundTasks(),
                db,
                "local",
                backend="kimi",
            )
        )

    assert response["status"] == "translating"
    assert schedule.call_args.args[2] == "kimi"
    assert db.add.call_args.args[0].backend == "kimi"


def test_native_kimi_builds_official_vendor_configuration(tmp_path):
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
            TranslationConfig(backend="kimi", api_key="moonshot-key"),
        )

    assert result.success is True
    assert vendor.call_args.kwargs == {
        "api_url": "https://api.moonshot.cn/v1",
        "api_key": "moonshot-key",
        "mode": "openai-compatible",
        "model": "kimi-k3",
        "source_lang": "en",
        "target_lang": "zh",
        "progress": False,
    }


def test_kimi_k3_batch_payload_obeys_fixed_sampling_constraints():
    translator = VendorTranslator(
        api_url="https://api.moonshot.cn/v1",
        api_key="moonshot-key",
        mode="openai-compatible",
        model="kimi-k3",
        progress=False,
    )
    response = {"choices": [{"message": {"content": '["译文"]'}}]}

    with patch.object(translator, "_post_json", return_value=response) as post_json:
        assert translator.translate_batch(["Source"]) == ["译文"]

    url, payload = post_json.call_args.args
    assert url == "https://api.moonshot.cn/v1/chat/completions"
    assert payload["model"] == "kimi-k3"
    assert payload["reasoning_effort"] == "max"
    assert payload["max_completion_tokens"] == translator.max_output_tokens
    assert "temperature" not in payload
    assert "max_tokens" not in payload


def test_kimi_k3_plain_fallback_obeys_fixed_sampling_constraints():
    translator = VendorTranslator(
        api_url="https://api.moonshot.cn/v1",
        api_key="moonshot-key",
        mode="openai-compatible",
        model="kimi-k3",
        progress=False,
    )
    response = {"choices": [{"message": {"content": "译文"}}]}

    with patch.object(translator, "_post_json", return_value=response) as post_json:
        assert translator._translate_openai_plain("Source") == "译文"

    payload = post_json.call_args.args[1]
    assert payload["reasoning_effort"] == "max"
    assert payload["max_completion_tokens"] == translator.max_output_tokens
    assert "temperature" not in payload
    assert "max_tokens" not in payload


def test_non_kimi_openai_payload_is_unchanged():
    translator = VendorTranslator(
        api_url="https://api.openai.com/v1",
        api_key="openai-key",
        mode="openai-compatible",
        model="gpt-4o-mini",
        progress=False,
    )
    response = {"choices": [{"message": {"content": '["译文"]'}}]}

    with patch.object(translator, "_post_json", return_value=response) as post_json:
        translator.translate_batch(["Source"])

    payload = post_json.call_args.args[1]
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == translator.max_output_tokens
    assert "max_completion_tokens" not in payload
    assert "reasoning_effort" not in payload


def test_reader_exposes_kimi_k3_and_forwards_selected_backend():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert 'id="translation-backend"' in html
    assert '<option value="kimi">Kimi K3</option>' in html
    assert "function getTranslationBackend()" in js
    assert (
        "doTranslateDirect(currentPaper.id, getTranslationBackend(), quality, options);"
        in js
    )
