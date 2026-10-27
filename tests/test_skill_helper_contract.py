"""Exercise published helper argv/paths and its shared strict QA policy offline."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf_zh_translator import pdf_layout, visual_qa

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/paper-translate/scripts/translate_one.sh"


@pytest.fixture
def helper_environment(tmp_path):
    repo = tmp_path / "repository"
    executable = repo / ".venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text(f'''#!{sys.executable}
import json,os,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
if args[0]=='-c':
    raise SystemExit(subprocess.run([sys.executable,*args]).returncode)
with open(os.environ['HELPER_TRACE'],'a') as handle:
    handle.write(json.dumps(args)+'\\n')
if args[:3]==['-m','pdf_zh_translator','translate']:
    Path(args[4]).write_bytes(b'generated specimen')
''')
    executable.chmod(0o700)
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "paper.pdf").write_bytes(b"source specimen")
    trace = tmp_path / "trace.jsonl"
    environment = {key: value for key, value in os.environ.items()
                   if "API_KEY" not in key and "TRANSLATE_HOME" not in key}
    environment.update(SUPER_TRANSLATE_HOME=str(repo), HELPER_TRACE=str(trace))
    return caller, environment, trace


def invoke_helper(fixture, *args, **variables):
    caller, environment, trace = fixture
    environment.update(variables)
    result = subprocess.run(
        ["bash", str(HELPER), "paper.pdf", "result.pdf", *args], cwd=caller,
        env=environment, capture_output=True, text=True,
    )
    calls = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    return result, calls


def test_helper_resolves_paths_before_cd_and_reports_beside_output(helper_environment):
    caller, _, _ = helper_environment
    result, calls = invoke_helper(helper_environment, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert calls[0][3:5] == [str(caller / "paper.pdf"), str(caller / "result.pdf")]
    assert calls[1][1:] == [str(caller / "paper.pdf"), str(caller / "result.pdf"),
                           str(caller / "result.strict.json")]
    assert (caller / "result.pdf").exists()
    assert (caller / "paper.pdf").read_bytes() == b"source specimen"
    assert "真实翻译验收" in result.stdout


@pytest.mark.parametrize("mode_args", [
    ["--api-mode", "openai-compatible"], ["--api-mode=openai-compatible"],
])
def test_custom_endpoint_never_implicitly_uses_deepseek_key(helper_environment, mode_args):
    result, calls = invoke_helper(
        helper_environment, *mode_args, "--api-url", "https://provider.invalid/v1",
        PAPER_CHINA_DEEPSEEK_API_KEY="dummy-private-deepseek",
    )
    assert result.returncode != 0 and not calls
    assert "--api-key-env" in result.stderr
    assert "dummy-private-deepseek" not in result.stderr + result.stdout


def test_explicit_custom_key_is_preserved_and_never_overridden(helper_environment):
    result, calls = invoke_helper(
        helper_environment, "--api-mode", "openai-compatible", "--api-key-env", "OWN_KEY",
        OWN_KEY="dummy-own", PAPER_CHINA_DEEPSEEK_API_KEY="dummy-deepseek",
    )
    assert result.returncode == 0
    assert "PAPER_CHINA_DEEPSEEK_API_KEY" not in calls[0]
    assert calls[0].count("--api-key-env") == 1
    assert "OWN_KEY" in calls[0]


@pytest.mark.parametrize("severity,code,score,want", [
    ("warning", "text_overlap", 0.9, 1),
    ("warning", "preserved_region_changed", 0.9, 1),
    ("error", "untranslated_block", 0.9, 1),
    ("warning", "advisory_only", 0.54, 1),
    ("warning", "advisory_only", 0.9, 0),
])
def test_skill_full_gate_rejects_actionable_warnings_or_low_visual_score(
    tmp_path, monkeypatch, severity, code, score, want,
):
    path = ROOT / "skills/paper-translate/scripts/verify_one.py"
    spec = importlib.util.spec_from_file_location("verify_skill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original, translated, report = [tmp_path / name for name in ("a.pdf", "b.pdf", "qa.json")]
    original.write_bytes(b"source")
    translated.write_bytes(b"target")
    monkeypatch.setattr(sys, "argv", [str(path), str(original), str(translated), str(report)])
    issue = pdf_layout.TranslationIssue(page=1, code=code, message="fixture", severity=severity)
    monkeypatch.setattr(pdf_layout, "verify_translation_issues", lambda *a: [issue])
    monkeypatch.setattr(
        visual_qa, "score_visual_layout", lambda *a: SimpleNamespace(overall_score=score),
    )
    assert module.main() == want
    result = json.loads(report.read_text())
    assert result["strict_pass"] is (want == 0)
    assert result["issues"][0]["code"] == code


def test_cache_and_font_paths_remain_relative_to_caller(helper_environment):
    caller, _, _ = helper_environment
    result, calls = invoke_helper(
        helper_environment, "--api-mode=cache-only", "--cache-file", "cache.jsonl",
        "--font-file=font.otf",
    )
    assert result.returncode == 0
    assert str(caller / "cache.jsonl") in calls[0]
    assert f"--font-file={caller / 'font.otf'}" in calls[0]


def test_custom_url_requires_explicit_credential_even_in_deepseek_mode(helper_environment):
    result, calls = invoke_helper(
        helper_environment, "--api-url=https://other.invalid/v1",
        PAPER_CHINA_DEEPSEEK_API_KEY="dummy-private-deepseek",
    )
    assert result.returncode != 0 and not calls
    assert "--api-key-env" in result.stderr
