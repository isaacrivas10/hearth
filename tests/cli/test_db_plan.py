"""Tests for `hearth db plan`."""

from __future__ import annotations

from typer.testing import CliRunner

from hearth.cli import app


def test_db_plan_no_pending(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Initialize: kernel tables only, no plugins shipping migrations yet.
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "plan"])
    assert result.exit_code == 0
    assert "No pending migrations" in result.output


def test_db_plan_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "plan"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
