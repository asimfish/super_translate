from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_server_startup_is_loopback_only_without_legacy_frp():
    startup = (ROOT / "scripts/start_server.sh").read_text(encoding="utf-8")

    assert "--host 127.0.0.1" in startup
    assert "--host 0.0.0.0" not in startup
    assert "frpc" not in startup
