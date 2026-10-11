"""Browser acceptance for eight providers, drafts, discovery and custom IDs."""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--artifacts", type=Path, required=True)
    options = parser.parse_args()
    options.artifacts.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[3]
    from app.core.provider_credentials import PROVIDER_SPECS

    catalogs = [
        {
            "provider": provider,
            "label": spec.label,
            "default_model": spec.default_model,
            "selected_model": spec.default_model,
            "models": [spec.default_model, f"{provider}-future-fast", f"{provider}-future-pro"],
            "model_options": [
                {"id": spec.default_model, "availability": "reference", "description": "默认配置"},
                {
                    "id": f"{provider}-future-fast",
                    "availability": "listed",
                    "description": "官方接口",
                },
                {
                    "id": f"{provider}-future-pro",
                    "availability": "listed",
                    "description": "官方接口",
                },
            ],
            "source": "provider",
            "refreshed_at": "2026-10-10T00:00:00Z",
            "warning": "",
        }
        for provider, spec in PROVIDER_SPECS.items()
    ]
    statuses = [
        {
            "provider": provider,
            "configured": True,
            "model": spec.default_model,
            "label": spec.label,
            "source": "personal",
            "key_hint": "••••test",
        }
        for provider, spec in PROVIDER_SPECS.items()
    ]
    saved = []
    checks = {"catalog_gets": 0, "forced_refreshes": 0}
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.clock.install()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def assets(route):
            path = route.request.url.removeprefix("http://providers.test/")
            file = root / "app" / (path or "static/index.html")
            if options.assets and file.name in {"index.html", "style.css", "app.js"}:
                file = options.assets / file.name
            route.fulfill(path=str(file), content_type=mimetypes.guess_type(file.name)[0])

        def api(route):
            path = route.request.url.removeprefix("http://providers.test")
            method = route.request.method
            if path == "/api/provider-credentials/models":
                checks["catalog_gets"] += 1
                route.fulfill(json=catalogs)
            elif path.startswith("/api/provider-credentials/models/"):
                checks["forced_refreshes"] += 1
                provider = path.split("/")[-2]
                route.fulfill(json=next(item for item in catalogs if item["provider"] == provider))
            elif path == "/api/provider-credentials":
                route.fulfill(json=statuses)
            elif path.startswith("/api/provider-credentials/") and method == "PUT":
                provider = path.split("/")[-1]
                body = route.request.post_data_json
                saved.append((provider, body))
                status = next(item for item in statuses if item["provider"] == provider)
                status["model"] = body["model"]
                next(item for item in catalogs if item["provider"] == provider)[
                    "selected_model"
                ] = body["model"]
                route.fulfill(json=status)
            elif path == "/api/stats":
                route.fulfill(json={"total_papers": 0, "completed_translations": 0})
            else:
                route.fulfill(json={"papers": [], "total": 0, "offset": 0, "limit": 50})

        page.route("http://providers.test/**", assets)
        page.route("http://providers.test/api/**", api)
        page.goto("http://providers.test/")
        page.locator("#btn-provider-settings").click()
        expect(page.locator(".provider-row")).to_have_count(8)
        expect(page.locator('[data-provider="gemini"] .provider-model-meta')).to_contain_text(
            "账号接口"
        )
        row = page.locator('[data-provider="gemini"]')
        row.locator(".provider-model").select_option("gemini-future-fast")
        row.locator(".provider-key").fill("fake-draft-secret")
        row.locator(".provider-model-refresh").click()
        expect(row.locator(".provider-model-refresh")).to_be_enabled()
        expect(row.locator(".provider-model")).to_have_value("gemini-future-fast")
        expect(row.locator(".provider-key")).to_have_value("fake-draft-secret")
        row.locator(".provider-model-search").fill("future-pro")
        expect(row.locator(".provider-model option")).to_have_count(2)
        expect(row.locator(".provider-model")).to_have_value("gemini-future-fast")
        row.locator(".provider-model-custom").fill("gemini-next-release")
        row.locator('[data-action="save-provider-credential"]').click()
        expect(row.locator('[data-action="save-provider-credential"]')).to_be_enabled()
        expect(row.locator(".provider-model")).to_have_value("gemini-next-release")
        expect(row.locator(".provider-key")).to_have_value("")
        assert saved[-1] == (
            "gemini",
            {"model": "gemini-next-release", "api_key": "fake-draft-secret"},
        )
        doubao = page.locator('[data-provider="doubao"]')
        doubao.locator(".provider-model-custom").fill("ep-20261010-private")
        doubao.locator('[data-action="save-provider-credential"]').click()
        expect(doubao.locator('[data-action="save-provider-credential"]')).to_be_enabled()
        assert saved[-1] == ("doubao", {"model": "ep-20261010-private"})
        # Fast-forward only the page clock to prove automatic checks and teardown.
        count = checks["catalog_gets"]
        page.clock.run_for(60001)
        expect(row.locator(".provider-model-meta")).to_contain_text("账号接口")
        assert checks["catalog_gets"] > count
        page.screenshot(path=str(options.artifacts / "settings-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator(".provider-dialog").evaluate("(el) => el.scrollWidth <= el.clientWidth")
        page.screenshot(path=str(options.artifacts / "settings-mobile.png"), full_page=True)
        page.locator('[data-action="hide-provider-settings"]').click()
        count = checks["catalog_gets"]
        page.clock.run_for(120001)
        assert checks["catalog_gets"] == count
        assert page.evaluate("providerCatalogTimer === null")
        assert not errors, errors
        browser.close()
    (options.artifacts / "checks.json").write_text(json.dumps(checks, indent=2))
    print(
        "Eight-provider browser journey passed: "
        "search, custom IDs, drafts, save, auto refresh, mobile."
    )


if __name__ == "__main__":
    run()
