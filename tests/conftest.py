"""Shared fixtures for the hearth test suite."""

from __future__ import annotations

import shutil
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest


class _PluginBuilder(Protocol):
    plugin: str
    package_dir: Path

    def add_revision(self, *, revision: str, down_revision: str | None, body: str) -> None: ...


@pytest.fixture
def tmp_plugin_with_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_PluginBuilder]:
    """Create a tmp Python package with a `migrations/` directory and an
    importable module. The builder lets tests append revision files."""

    plugin = f"hearth_test_plug_{tmp_path.name.replace('-', '_')}"
    package_dir = tmp_path / plugin
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "migrations").mkdir()
    (package_dir / "migrations" / "__init__.py").write_text("")

    monkeypatch.syspath_prepend(str(tmp_path))

    class _Builder:
        def __init__(self) -> None:
            self.plugin = plugin
            self.package_dir = package_dir

        def add_revision(self, *, revision: str, down_revision: str | None, body: str) -> None:
            content = textwrap.dedent(body).lstrip("\n")
            (package_dir / "migrations" / f"{revision}_test.py").write_text(content)

    yield _Builder()

    # Tear down: drop the module so subsequent tests get a clean slate.
    for mod in list(sys.modules):
        if mod == plugin or mod.startswith(f"{plugin}."):
            del sys.modules[mod]
    shutil.rmtree(package_dir, ignore_errors=True)


@pytest.fixture
def example_revision_body() -> str:
    """A simple revision authors can copy into add_revision()."""
    return """
        from alembic import op
        import sqlalchemy as sa
        from hearth.migrations import OpSummary

        revision = "{revision}"
        down_revision = {down_revision!r}
        branch_labels = ({branch!r},)
        depends_on = None

        HEARTH_UPGRADE_OPS = [
            OpSummary(kind="CreateTable", desc="CREATE TABLE {table}", destructive=False),
        ]
        HEARTH_DOWNGRADE_OPS = [
            OpSummary(kind="DropTable", desc="DROP TABLE {table}", destructive=True),
        ]


        def upgrade() -> None:
            op.create_table(
                "{table}",
                sa.Column("id", sa.BigInteger, primary_key=True),
                sa.Column("name", sa.String, nullable=True),
            )


        def downgrade() -> None:
            op.drop_table("{table}")
    """
