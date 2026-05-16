"""Tests for `hearth auth bootstrap`."""

from __future__ import annotations

import sqlite3

from typer.testing import CliRunner

from hearth.cli import app


def test_bootstrap_creates_admin(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "auth_bootstrap.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0

    result = runner.invoke(
        app,
        ["auth", "bootstrap", "--email", "admin@example.com"],
        input="hunter2\nhunter2\n",
    )
    assert result.exit_code == 0
    combined = result.stdout + (result.stderr or "")
    assert "admin@example.com" in combined

    # Inspect DB directly to confirm a wildcard permission + user exist.
    # Composite column names use single underscore: `email_raw`, `name_resource`,
    # `name_action` — see `_install_composite` in hearth/primitives/entity.py.
    db_file = url.replace("sqlite:///", "")
    con = sqlite3.connect(db_file)
    try:
        users = con.execute("SELECT email_raw FROM auth__user").fetchall()
        perms = con.execute("SELECT name_resource, name_action FROM auth__permission").fetchall()
    finally:
        con.close()

    assert ("admin@example.com",) in users
    assert ("*", "*") in perms


def test_bootstrap_refuses_when_wildcard_exists(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "auth_bootstrap2.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    runner.invoke(
        app,
        ["auth", "bootstrap", "--email", "first@example.com"],
        input="x\nx\n",
    )

    result = runner.invoke(
        app,
        ["auth", "bootstrap", "--email", "second@example.com"],
        input="y\ny\n",
    )
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert "already bootstrapped" in combined.lower()
