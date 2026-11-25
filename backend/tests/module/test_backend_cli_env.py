"""Maintenance commands use the same database as the local app (scripts/local-env.sh).

A stub ``uv`` on PATH records what would run, so no database or provider is touched.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def run_cli(tmp_path: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "uv"
    stub.write_text('#!/usr/bin/env bash\nprintf "url=%s\\n" "$DATABASE_URL"\nprintf "cwd=%s\\n" "$PWD"\nprintf "args=%s\\n" "$*"\n')
    stub.chmod(0o755)
    base = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    base.update({"PATH": f"{tmp_path}:{os.environ['PATH']}", "USER": "localdev", "LOCAL_ENV_FILE": str(tmp_path / "missing.env"), **env})
    return subprocess.run(
        [str(REPO / "scripts" / "backend-cli.sh"), *args],
        env=base,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_cli_gets_the_same_default_database_as_dev_sh(tmp_path: Path) -> None:
    result = run_cli(tmp_path, {}, "app.shopping.reconcile_unknown", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "url=postgresql+asyncpg://localdev@127.0.0.1:5432/equal_ai" in result.stdout
    assert f"cwd={REPO / 'backend'}" in result.stdout
    assert "args=run --with-requirements requirements.txt python -m app.shopping.reconcile_unknown --dry-run" in result.stdout
    assert result.stderr.strip() == "database=127.0.0.1:5432/equal_ai"


def test_the_repo_env_file_is_used_when_the_shell_has_none(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('OTHER=1\nDATABASE_URL="postgresql+asyncpg://gs@127.0.0.1:5432/gharsaathi"\n')

    result = run_cli(tmp_path, {"LOCAL_ENV_FILE": str(env_file)}, "app.shopping.reconcile_unknown", "--dry-run")

    assert "url=postgresql+asyncpg://gs@127.0.0.1:5432/gharsaathi" in result.stdout
    assert result.stderr.strip() == "database=127.0.0.1:5432/gharsaathi"


def test_an_exported_database_url_wins_and_credentials_are_not_printed(tmp_path: Path) -> None:
    url = "postgresql+asyncpg://someone:not-a-real-password@db.test:5433/other"

    result = run_cli(tmp_path, {"DATABASE_URL": url}, "app.shopping.reconcile_unknown", "--dry-run")

    assert f"url={url}" in result.stdout
    assert "not-a-real-password" not in result.stderr
    assert result.stderr.strip() == "database=db.test:5433/other"


def test_dev_sh_and_the_cli_share_one_definition() -> None:
    dev = (REPO / "scripts" / "dev.sh").read_text()
    assert 'source "${REPO_ROOT}/scripts/local-env.sh"' in dev
    assert "export_local_database_url" in dev
    assert "postgresql+asyncpg://" not in dev
    makefile = (REPO / "Makefile").read_text()
    assert "./scripts/backend-cli.sh app.shopping.reconcile_unknown $(ARGS)" in makefile
