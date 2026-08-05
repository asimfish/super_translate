"""Subprocess translation worker.

Runs one translation job in a dedicated process so a native-layer crash
(PyMuPDF/pikepdf heap corruption) kills only the worker, never the web
server. The parent passes a JSON job spec; progress is appended to
.worker_progress.jsonl and the final outcome to .worker_result.json inside
the job's output directory. faulthandler stays on so a crash still leaves a
Python stack in the server log.
"""

from __future__ import annotations

import faulthandler
import json
import sys
from pathlib import Path


def main() -> int:
    faulthandler.enable()
    spec_path = Path(sys.argv[1])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    output_dir = Path(spec["output_dir"])
    progress_file = output_dir / ".worker_progress.jsonl"
    result_file = output_dir / ".worker_result.json"

    from app.services.translator import QualityPreset, TranslationConfig, translate_pdf_sync

    config_data = dict(spec["config"])
    config_data["quality"] = QualityPreset(config_data.get("quality", "balanced"))
    config = TranslationConfig(**config_data)

    def on_progress(pct: float) -> None:
        with progress_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"progress": pct}) + "\n")

    try:
        result = translate_pdf_sync(
            Path(spec["input_path"]),
            output_dir,
            config,
            on_progress,
        )
    except Exception as exc:  # noqa: BLE001 - last-resort capture for the parent
        result_file.write_text(
            json.dumps({"mono_path": None, "dual_path": None, "error": str(exc)}),
            encoding="utf-8",
        )
        return 1

    result_file.write_text(
        json.dumps(
            {
                "mono_path": str(result.mono_path) if result.mono_path else None,
                "dual_path": str(result.dual_path) if result.dual_path else None,
                "error": result.error,
            }
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
