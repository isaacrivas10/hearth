"""Tests for hearth web CLI commands."""

from typer.testing import CliRunner

from hearth_web.cli import web_app

runner = CliRunner()


def test_create_app_builds_clean(monkeypatch):
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "k")
    from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
    from hearth_web.app import create_app
    from plugins.web.tests.conftest import make_registry

    app = create_app(
        engine=make_async_engine("sqlite:///:memory:"), registry=make_registry(), modules=[]
    )
    assert app.title == "Hearth"


def test_serve_missing_secret_key_exits(monkeypatch):
    monkeypatch.delenv("HEARTH_WEB_SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./x.db")
    result = runner.invoke(web_app, ["serve"])
    assert result.exit_code != 0
    assert "HEARTH_WEB_SECRET_KEY" in result.output
