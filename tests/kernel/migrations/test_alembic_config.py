"""Tests for the dynamic Alembic config builder."""

from __future__ import annotations

from pathlib import Path

from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.registry import Registry


def test_build_config_sets_render_as_batch_for_sqlite(tmp_path: Path) -> None:
    registry = Registry.build()
    cfg = build_config(registry, f"sqlite:///{tmp_path}/x.db")
    # render_as_batch is set in the EnvironmentContext; we expose it via
    # config attributes the alembic_config module also reads at apply time.
    assert cfg.get_main_option("hearth.render_as_batch") == "true"


def test_build_config_omits_render_as_batch_for_postgres() -> None:
    registry = Registry.build()
    cfg = build_config(registry, "postgresql://user:pw@host/db")
    assert cfg.get_main_option("hearth.render_as_batch") in (None, "false")


def test_build_config_sets_version_locations_per_plugin(tmp_path: Path) -> None:
    registry = Registry.build()
    cfg = build_config(registry, f"sqlite:///{tmp_path}/x.db")
    locations = cfg.get_main_option("version_locations") or ""
    # Each installed plugin contributes at most one comma-separated entry;
    # plugins without a `migrations/` dir are silently skipped.
    parts = [p.strip() for p in locations.split(",") if p.strip()]
    assert len(parts) <= len(registry.plugins)
    for part in parts:
        assert part.endswith("/migrations"), f"version_location should point at migrations/: {part}"
