"""Build an in-memory Alembic `Config` from the kernel's plugin registry.

The kernel never ships an `alembic.ini` or an `env.py`; everything Alembic
needs at runtime is constructed here. One `Config` per kernel process,
multi-base via `version_locations` (one path per plugin), branch labels
declared in each revision file.
"""

from __future__ import annotations

import importlib.resources
import shutil
import tempfile
from pathlib import Path

from alembic.config import Config

from hearth.kernel.persistence import METADATA
from hearth.kernel.registry import Registry

# Module-level cache so we materialize env.py once per process.
_env_dir_cache: Path | None = None

# Minimal Alembic revision template — used when `alembic revision` autogenerates
# a new file. Plugin-authored revisions are hand-written and do not depend on
# this template.
_SCRIPT_MAKO = '''\
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


def _materialize_env_dir() -> Path:
    """Copy the kernel's env.py into a tmp dir Alembic can use as
    `script_location`. Re-used across calls in the same process."""
    global _env_dir_cache
    if _env_dir_cache is not None and _env_dir_cache.exists():
        return _env_dir_cache
    env_dir = Path(tempfile.mkdtemp(prefix="hearth_alembic_"))
    src = importlib.resources.files("hearth.kernel.migrations").joinpath("_env.py")
    shutil.copy(str(src), env_dir / "env.py")
    # Alembic also expects a script.py.mako template alongside env.py; ship a
    # minimal one so `alembic revision` (used in Phase F) works out of the box.
    (env_dir / "script.py.mako").write_text(_SCRIPT_MAKO)
    _env_dir_cache = env_dir
    return env_dir


def _plugin_migrations_dir(module_name: str) -> Path:
    """Resolve `<plugin_module>/migrations/` to a filesystem path."""
    return Path(str(importlib.resources.files(module_name).joinpath("migrations")))


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _to_sync_url(url: str) -> str:
    """Alembic uses sync drivers internally. Translate aiosqlite/asyncpg URLs
    to their sync equivalents."""
    if url.startswith("sqlite+aiosqlite"):
        return url.replace("sqlite+aiosqlite", "sqlite", 1)
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql", 1)
    return url


def build_config(registry: Registry, engine_url: str) -> Config:
    """Construct the Alembic `Config` used for plan/apply/generate.

    - `script_location`: a tmp dir populated with the kernel's programmatic
      env.py and a minimal `script.py.mako`. Materialized once per process.
    - `version_locations`: comma-separated list of `<plugin>/migrations/`
      paths for every installed plugin that ships a `migrations/` directory.
      Plugins without one are silently skipped.
    - `sqlalchemy.url`: the engine URL translated to its sync form
      (Alembic does not use async drivers directly).
    - `target_metadata`: the kernel's merged plugin-entity MetaData
      (set as an attribute, not a config option — Alembic reads it from
      `config.attributes` during autogenerate).
    - `hearth.render_as_batch`: "true" iff the engine URL targets SQLite.
      env.py reads this and passes `render_as_batch=True` to
      `context.configure(...)` so `op.batch_alter_table(...)` works.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_materialize_env_dir()))
    cfg.set_main_option("sqlalchemy.url", _to_sync_url(engine_url))

    locations: list[str] = []
    for _alias, info in sorted(registry.plugins.items()):
        try:
            path = _plugin_migrations_dir(info.module)
        except (ModuleNotFoundError, FileNotFoundError):
            # Plugin has no migrations/ directory yet — that's fine, skip it.
            continue
        if path.is_dir():
            locations.append(str(path))
    cfg.set_main_option("version_locations", ",".join(locations))

    cfg.attributes["target_metadata"] = METADATA
    cfg.set_main_option(
        "hearth.render_as_batch",
        "true" if _is_sqlite(engine_url) else "false",
    )
    return cfg
