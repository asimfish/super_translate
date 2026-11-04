"""Auth rejection must bypass HTTP retries, format fallbacks and engine repair."""

import io
import json
import urllib.error

import pytest

from app.services.translation_recovery import provider_action_required
from pdf_zh_translator.translators import (
    ProviderAuthenticationError,
    VendorTranslator,
)


@pytest.mark.parametrize("error,expected", [
    ("Translation API request failed: HTTP Error 401: Authorization Required", "401"),
    ("Error code: 401 - invalid_api_key", "401"),
    ("status_code=403", "403"),
    ("authentication_error: incorrect api key sk-do-not-retain-this", "401"),
    ("DeepSeek 模型接口鉴权失败（HTTP 401）。请在 API 设置中更新有效的 API Key", "401"),
    ("HTTP Error 429: Too Many Requests", None),
    ("HTTP Error 503: Service Unavailable", None),
    ("Page 401 has untranslated blocks", None),
    ("HTTP Error 4010", None),
    ("Connection timed out", None),
    (None, None),
])
def test_only_auth_errors_require_credentials(error, expected):
    message = provider_action_required(error, "deepseek")
    if expected is None:
        assert message is None
    else:
        assert f"HTTP {expected}" in message and "DeepSeek" in message
        assert "API 设置" in message and "重试" in message
        assert "sk-do-not-retain-this" not in message


@pytest.mark.parametrize("mode", ["deepseek", "openai-compatible", "anthropic"])
@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("texts", [["Abstract"], ["Abstract", "Introduction"]])
def test_auth_rejection_never_falls_back_or_retries(monkeypatch, mode, status, texts):
    calls = []

    def reject(request, **kwargs):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, status, "Authorization Required", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", reject)
    monkeypatch.setattr("pdf_zh_translator.translators.time.sleep",
                        lambda _: pytest.fail("Auth rejection must not back off"))
    translator = VendorTranslator(api_url="https://example.test", api_key="fake-key",
                                  mode=mode, retries=2, progress=False)
    with pytest.raises(ProviderAuthenticationError, match=f"HTTP Error {status}"):
        translator.translate_batch(texts)
    assert len(calls) == 1


def test_transient_provider_failure_still_retries(monkeypatch):
    calls, sleeps = [], []

    def respond(request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)
        response = {"choices": [{"message": {"content": '["摘要"]'}}]}
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("urllib.request.urlopen", respond)
    monkeypatch.setattr("pdf_zh_translator.translators.time.sleep", sleeps.append)
    translator = VendorTranslator(api_url="https://example.test", mode="deepseek", progress=False)
    assert translator.translate_batch(["Abstract"]) == ["摘要"]
    assert len(calls) == 2 and sleeps == [0.8]
