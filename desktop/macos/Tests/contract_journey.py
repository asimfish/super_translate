"""Run the native GUI through actual API routes without real models or production data."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import fitz
import httpx


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--open-adobe", action="store_true")
    options = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    binary = (
        repo
        / "desktop/macos/build/Super Translate Floating.app/Contents/MacOS/SuperTranslateFloating"
    )
    if not binary.exists():
        raise SystemExit("Build the native app with desktop/macos/build.sh first")
    artifacts = options.artifacts or Path(tempfile.mkdtemp(prefix="supertranslate-desktop-"))
    artifacts.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server_url = f"http://127.0.0.1:{port}"
    source = artifacts / "synthetic.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((72, 100), "Floating PDF translation contract fixture")
        document.save(source)
    report = artifacts / "journey.json"
    snapshot = artifacts / "completed.png"
    with (artifacts / "server.log").open("w") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("serve_contract.py")),
                str(artifacts / "server"),
                str(port),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=server_url, timeout=5) as client:
                for _ in range(100):
                    try:
                        response = client.get("/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if server.poll() is not None:
                        raise RuntimeError("Isolated server failed; inspect server.log")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Isolated server did not start")
                assert client.get("/api/papers/").status_code == 401
                arguments = [
                    str(binary),
                    "--server",
                    server_url,
                    "--smoke-pdf",
                    str(source),
                    "--smoke-report",
                    str(report),
                    "--smoke-snapshot",
                    str(snapshot),
                    "--smoke-compact-snapshot",
                    str(artifacts / "compact.png"),
                    "--test-storage",
                    str(artifacts / "client"),
                    "--smoke-contract",
                ]
                if options.open_browser:
                    arguments.append("--smoke-open-browser")
                if options.open_adobe:
                    arguments.append("--smoke-open-adobe")
                environment = os.environ.copy()
                environment["SUPER_TRANSLATE_SMOKE_TOKEN"] = "desktop-local-fixture"
                native = subprocess.run(
                    arguments, env=environment, capture_output=True, text=True, timeout=90
                )
                (artifacts / "native.log").write_text(native.stdout + native.stderr)
                result = json.loads(report.read_text())
                assert native.returncode == 0 and result["status"] == "PASSED", result
                assert result["compact_panel"] is True
                assert not options.open_adobe or result["adobe_opened"] is True
                assert all(
                    result[key]
                    for key in [
                        "panel_floating",
                        "panel_transparent",
                        "panel_nonactivating",
                        "panel_visible",
                    ]
                )
                assert result["url_provider_drop"] is True
                assert len(result["contract_checks"]) == 21 and all(
                    result["contract_checks"].values()
                )
                output = Path(result["pdf"])
                assert output.stat().st_mode & 0o777 == 0o600
                assert output.parent.stat().st_mode & 0o777 == 0o700
                with fitz.open(output) as pdf:
                    assert pdf.page_count == 1 and "悬窗译文" in pdf[0].get_text()
                for state in (artifacts / "client").glob("*/jobs.json"):
                    assert state.stat().st_mode & 0o777 == 0o600
                    assert "desktop-local-fixture" not in state.read_text()
                    assert "fixture-password" not in state.read_text()
                other = {"Authorization": "Bearer desktop-other-fixture"}
                assert (
                    client.get(f"/api/papers/{result['paper_id']}", headers=other).status_code
                    == 404
                )
                assert (
                    client.get(
                        f"/api/papers/{result['paper_id']}/download/translated", headers=other
                    ).status_code
                    == 404
                )
                events = client.get("/__desktop_test/events").json()
                uploads = [
                    event
                    for event in events
                    if event["method"] == "POST" and event["path"] == "/api/papers/upload"
                ]
                starts = [
                    event
                    for event in events
                    if event["method"] == "POST" and event["path"].endswith("/translate")
                ]
                assert len(uploads) == 6 and len(starts) == 6, (
                    "Unexpected duplicate or invalid upload/start"
                )
                from urllib.parse import parse_qs

                assert any(
                    parse_qs(event["query"]).get("backend") == ["deepseek"]
                    and event.get("effective_model") == "deepseek-flash"
                    for event in starts
                ), "Selected DeepSeek Flash did not reach the authenticated translation route"
                assert all(event["authorized_header"] for event in uploads + starts)
                qa_reads = [event for event in events if event["path"].endswith("/qa-report")]
                assert qa_reads and all(event["authorized_header"] for event in qa_reads)
                for event in qa_reads:
                    assert client.get(event["path"], headers=other).status_code == 404
                assert not any(event["path"] == "/__desktop_test/leak" for event in events)
                assert not any("fixture" in event["query"] for event in events)
                result["api_uploads"] = len(uploads)
                result["api_translation_starts"] = len(starts)
                result["cross_account_api_denied"] = True
                result["private_pdf_permissions"] = True
                report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
                print(
                    json.dumps(
                        {
                            "status": "PASSED",
                            "report": str(report),
                            "snapshot": str(snapshot),
                            "adobe_available": result["adobe_available"],
                            "browser_opened": result["browser_opened"],
                        },
                        ensure_ascii=False,
                    )
                )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    run()
