"""Native drops and shipped browser UI against an isolated real API/SQLite."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import fitz
import httpx
from playwright.sync_api import sync_playwright


def run():
    repo = Path(__file__).resolve().parents[3]
    artifacts = Path(sys.argv[1]).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    title = "Molecular Systems and Chemical Representations"
    for name, body in [("source.pdf", "Version one"), ("changed.pdf", "Version two")]:
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 100), title, fontsize=20)
            page.insert_text((72, 180), body)
            doc.set_metadata({"title": title})
            doc.save(artifacts / name)
    with (artifacts / "server.log").open("w") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("serve_contract.py")),
                str(artifacts / "server"),
                str(port),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=origin, headers={"Authorization": "Bearer desktop-local-fixture"}
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if server.poll() is not None:
                        raise RuntimeError("Fixture server exited")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Fixture server did not start")
                env = os.environ.copy()
                env["SUPER_TRANSLATE_SMOKE_TOKEN"] = "desktop-local-fixture"
                binary = (
                    repo
                    / "desktop/macos/build/Super Translate Floating.app"
                    / "Contents/MacOS/SuperTranslateFloating"
                )
                result = subprocess.run(
                    [
                        str(binary),
                        "--server",
                        origin,
                        "--smoke-pdf",
                        str(artifacts / "source.pdf"),
                        "--smoke-report",
                        str(artifacts / "native.json"),
                        "--library-changed-pdf",
                        str(artifacts / "changed.pdf"),
                        "--test-storage",
                        str(artifacts / "client"),
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                (artifacts / "native.log").write_text(result.stdout + result.stderr)
                report = json.loads((artifacts / "native.json").read_text())
                assert result.returncode == 0 and report["status"] == "PASSED", report
                checks = report["library_checks"]
                assert all(v for k, v in checks.items() if isinstance(v, bool))
                papers = client.get("/api/papers/").json()["papers"]
                assert len(papers) == 2 and all(p["version_count"] == 2 for p in papers)
                assert (
                    sum(len(client.get(f"/api/papers/{p['id']}/jobs").json()) for p in papers) == 2
                )
                exported = Path(checks["export_path"])
                assert exported.stat().st_mode & 0o777 == 0o600
                assert exported.parent.stat().st_mode & 0o777 == 0o700
                with fitz.open(exported) as output:
                    assert "悬窗译文" in output[0].get_text()
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        executable_path=(
                            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                        ),
                        headless=True,
                    )
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    page.add_init_script(
                        "localStorage.setItem('paperChinaApiToken', 'desktop-local-fixture')"
                    )
                    page.goto(origin)
                    page.evaluate("async () => { await loadPapers(); }")
                    page.wait_for_function(
                        "() => document.querySelectorAll('#paper-list .paper-card').length === 2"
                    )
                    assert page.locator("#paper-list .paper-card").count() == 2
                    assert "内容版本" in page.locator("#paper-list").inner_text()
                    assert page.locator(".library-date-group").count() == 1
                    page.locator("#search-input").fill("renamed-copy.pdf")
                    page.wait_for_function(
                        "() => document.querySelectorAll('#paper-list .paper-card').length === 1"
                    )
                    assert "导入 2 次" in page.locator("#paper-list").inner_text()
                    page.locator("#library-sort").select_option("oldest")
                    page.wait_for_timeout(400)
                    assert (
                        page.evaluate(
                            "documentDownloadName(\"attachment; filename*=utf-8''"
                            '%E5%8C%96%E5%AD%A6.pdf")'
                        )
                        == "化学.pdf"
                    )
                    page.locator("#search-input").fill("")
                    page.wait_for_function(
                        "() => document.querySelectorAll('#paper-list .paper-card').length === 2"
                    )
                    page.screenshot(path=str(artifacts / "web-library.png"), full_page=True)
                    browser.close()
                (artifacts / "acceptance.json").write_text(
                    json.dumps(
                        {
                            "status": "PASSED",
                            "native_checks": checks,
                            "web_alias_search": True,
                            "web_version_badges": True,
                            "web_time_groups": True,
                            "web_unicode_download_name": True,
                            "duplicate_translation_not_restarted": True,
                            "translation_worker": (
                                "isolated fixture; model quality not measured here"
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                print("Native document library and shipped web journey PASSED")
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    run()
