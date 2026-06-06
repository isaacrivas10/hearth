"""Tests for `hearth db migrate`."""

from __future__ import annotations

from typer.testing import CliRunner

from hearth.cli import app


def test_db_migrate_no_pending(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 0
    assert "No pending migrations" in result.output


def test_db_migrate_dry_run_is_plan(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "migrate", "--dry-run"])
    assert result.exit_code == 0
    assert "No pending migrations" in result.output
