"""Tests for `hearth migrate check` (G1 surface)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from hearth.cli import app
from hearth.kernel.registry import PluginInfo, Registry


def _stub_registry_with_plugin(monkeypatch: pytest.MonkeyPatch, builder: object) -> None:
    """Inject `builder.plugin` into the registry that `check` builds.

    The check command resolves the plugin's migrations dir via
    `PluginInfo.install_path`, and resolves migration files for drift
    detection via `info.module` (through `build_config`'s
    `importlib.resources` lookup). `install_path` matches production
    (`Registry.build()`): the package's `__init__.py` file path, not
    the package directory.
    """
    plugin: str = builder.plugin  # type: ignore[attr-defined]
    package_dir = builder.package_dir  # type: ignore[attr-defined]
    real_build = Registry.build

    def fake_build() -> Registry:
        reg = real_build()
        reg.plugins[plugin] = PluginInfo(
            alias=plugin,
            package=plugin,
            version="0.0.1",
            module=plugin,
            install_path=str(package_dir / "__init__.py"),
            depends_on=[],
            extends=[],
            entities=[],
            actions=[],
            events=[],
        )
        return reg

    monkeypatch.setattr(Registry, "build", staticmethod(fake_build))


def test_check_passes_when_no_drift_and_hooks_consistent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_plugin_with_migrations: object,
) -> None:
    """Revision declares both `data_upgrade` and a matching
    `OpSummary(kind="DataMigration")` in `HEARTH_UPGRADE_OPS`. No entities
    are declared, so there is no schema drift either. Expected: exit 0."""
    builder = tmp_plugin_with_migrations
    builder.add_revision(  # type: ignore[attr-defined]
        revision="0001",
        down_revision=None,
        body=f"""
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)  # type: ignore[attr-defined]

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="DataMigration", desc="seed", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                pass

            def downgrade():
                pass

            async def data_upgrade(uow):
                pass
        """,
    )
    _stub_registry_with_plugin(monkeypatch, builder)

    runner = CliRunner()
    result = runner.invoke(app, ["migrate", "check", builder.plugin])  # type: ignore[attr-defined]
    assert result.exit_code == 0, result.output
    assert "no drift" in result.output


def test_check_fails_when_data_upgrade_present_but_opsummary_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_plugin_with_migrations: object,
) -> None:
    """Revision defines `data_upgrade` but `HEARTH_UPGRADE_OPS` has no
    `DataMigration` entry. Expected: exit 1 with a specific error message."""
    builder = tmp_plugin_with_migrations
    builder.add_revision(  # type: ignore[attr-defined]
        revision="0001",
        down_revision=None,
        body=f"""
            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)  # type: ignore[attr-defined]

            HEARTH_UPGRADE_OPS = []
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                pass

            def downgrade():
                pass

            async def data_upgrade(uow):
                pass
        """,
    )
    _stub_registry_with_plugin(monkeypatch, builder)

    runner = CliRunner()
    result = runner.invoke(app, ["migrate", "check", builder.plugin])  # type: ignore[attr-defined]
    assert result.exit_code == 1
    assert "defines data_upgrade but HEARTH_UPGRADE_OPS has no DataMigration" in result.output
