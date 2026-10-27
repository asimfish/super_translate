"""The documented account command must work before the first server start."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_account_cli_bootstraps_a_fresh_install_without_running_the_server(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("PAPER_CHINA_")
    }
    environment.update({
        "PAPER_CHINA_BASE_DIR": str(tmp_path),
        "PAPER_CHINA_USER_PASSWORD": "installation-fixture-password",
    })
    command = [sys.executable, str(root / "scripts/manage_users.py")]
    # Imports and interpreter shutdown can be delayed while the full PDF suite
    # is rendering. This is an installation correctness check, not a benchmark.
    process_timeout = 120
    created = subprocess.run(
        [*command, "create", "reader", "--scope", "local"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=process_timeout,
    )
    assert created.returncode == 0, created.stderr
    assert "created user reader scope=local" in created.stdout
    assert environment["PAPER_CHINA_USER_PASSWORD"] not in created.stdout + created.stderr

    database = tmp_path / "data/paper_china.db"
    assert database.is_file()
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"papers", "translation_jobs", "users", "provider_credentials"} <= tables
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    listed = subprocess.run(
        [*command, "list"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=process_timeout,
    )
    assert listed.returncode == 0, listed.stderr
    assert "reader\tscope=local" in listed.stdout
