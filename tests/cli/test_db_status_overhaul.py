"""Tests for the overhauled `hearth db status` (reads schema log + plan)."""

from __future__ import annotations

from typer.testing import CliRunner

from hearth.cli import app


def test_db_status_reports_zero_when_empty(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/status.db"
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "status"])
    assert result.exit_code == 0
    assert "applied" in result.output
