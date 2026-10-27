"""Exercise real WidgetStore with legacy task caches against an isolated HTTP fixture.

Run: python3 desktop/macos/Tests/task_reconciliation_journey.py /private/artifact-dir
The fixture performs no production requests, opens no reader and uses temporary caches.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
PAPERS = {}
for paper_id in ("ab1b0a7b2f5b", "f29440f9d7ba", "f0354b8795f3",
                 "012345abcde1", "012345abcde2", "012345abcde3"):
    PAPERS[paper_id] = {
        "id": paper_id, "title": "Full scientific paper title", "translation_status": "completed",
        "translation_progress": 1, "translation_stage": "已完成", "translation_eta": "",
        "translation_error": None, "has_translated": True, "has_dual": True,
        "has_qa_report": True, "created_at": "2026-10-11T03:12:00+00:00",
        "original_filename": "source.pdf", "version_count": 1, "version_number": 1,
    }
for paper_id in ("ab1b0a7b2f5b", "f29440f9d7ba"):
    PAPERS[paper_id]["canonical_paper_id"] = "f0354b8795f3"
for number, paper_id in enumerate(("012345abcde1", "012345abcde2"), 1):
    PAPERS[paper_id].update(title="Same title", version_count=2, version_number=number)
PAPERS["012345abcde3"].update(
    title="Repair paper", translation_status="repairing", translation_progress=0.91,
    translation_stage="等待系统修复", translation_error="第 17 页 table_structure_mismatch；译文已保留")
for paper_id in ("012345abcde4", "012345abcde5"):
    PAPERS[paper_id] = dict(PAPERS["012345abcde3"], id=paper_id)
REQUESTS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        REQUESTS.append({"method": "GET", "path": self.path.split("?")[0]})
        if self.headers.get("Authorization") != "Bearer fixture-token":
            return self.reply({"detail": "Unauthorized"}, 401)
        path = self.path.split("?")[0]
        if path == "/api/papers/":
            return self.reply({"papers": [], "total": 0, "offset": 0, "limit": 50})
        if path in ("/api/provider-credentials", "/api/provider-credentials/models"):
            return self.reply([])
        if path.endswith("/qa-report"):
            return self.reply({"status": "failed", "issues": [{
                "page": 17, "code": "table_structure_mismatch", "message": "第 17 页未通过质检",
                "severity": "error"}]})
        return self.reply(PAPERS.get(path.rsplit("/", 1)[-1], {"detail": "Not found"}),
                          200 if path.rsplit("/", 1)[-1] in PAPERS else 404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        REQUESTS.append({"method": "POST", "path": self.path})
        if self.path == "/api/papers/012345abcde5/cancel":
            return self.reply({"detail": "Fixture cancellation unavailable"}, 503)
        if self.path == "/api/papers/012345abcde4/cancel":
            PAPERS["012345abcde4"]["translation_status"] = "pending"
            return self.reply({"ok": True, "status": "cancelled"})
        if self.path == "/api/papers/upload":
            return self.reply(dict(PAPERS["012345abcde3"], duplicate=True))
        return self.reply({"ok": True, "status": "translating"})


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    package = output / "package"
    shutil.copytree(ROOT / "Sources/FloatingCore", package / "Sources/FloatingCore")
    (package / "Sources/FloatingAppSupport").mkdir(parents=True)
    shutil.copyfile(ROOT / "Sources/FloatingApp/WidgetStore.swift",
                    package / "Sources/FloatingAppSupport/WidgetStore.swift")
    shutil.copytree(ROOT / "Tests/FloatingReconciliationTests", package / "Tests/FloatingReconciliationTests")
    (package / "Package.swift").write_text('''// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "FloatingTaskReplay", platforms: [.macOS(.v13)], targets: [
    .target(name: "FloatingCore"), .target(name: "FloatingAppSupport", dependencies: ["FloatingCore"]),
    .testTarget(name: "FloatingReconciliationTests", dependencies: ["FloatingCore", "FloatingAppSupport"]),
])
''')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = dict(os.environ, PAPER_TASK_FIXTURE_ORIGIN=f"http://127.0.0.1:{server.server_port}")
    try:
        with (output / "swift.log").open("w") as log:
            result = subprocess.run(["swift", "test", "--package-path", str(package)],
                                    env=environment, stdout=log, stderr=subprocess.STDOUT)
    finally:
        server.shutdown()
        server.server_close()
    (output / "requests.json").write_text(json.dumps(REQUESTS, indent=2))
    starts = [request for request in REQUESTS
              if request["method"] == "POST" and request["path"].split("?")[0].endswith("/translate")]
    expected_starts = [request for request in starts
                       if request["path"].split("?")[0] == "/api/papers/012345abcde4/translate"]
    unexpected_starts = [request for request in starts if request not in expected_starts]
    summary = {"swift_exit": result.returncode,
               "unexpected_translation_starts": len(unexpected_starts),
               "repair_retry_starts": len(expected_starts),
               "production_requests": 0}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    return result.returncode or (1 if unexpected_starts or len(expected_starts) != 1 else 0)


if __name__ == "__main__":
    raise SystemExit(main())
