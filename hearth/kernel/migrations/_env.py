"""Programmatic env.py for the kernel's Alembic environment.

The kernel ships this file as a Python module; `build_config()` copies
it into a tmp `script_location` directory so Alembic can find it. The
env.py reads `target_metadata` from `config.attributes` (set by
`build_config()`) and `hearth.render_as_batch` from the main options.
"""

from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool


def run_migrations_offline() -> None:
    config: Any = context.config
    url = config.get_main_option("sqlalchemy.url")
    target_metadata = config.attributes.get("target_metadata")
    render_as_batch = config.get_main_option("hearth.render_as_batch") == "true"
    context.configure(  # pyright: ignore[reportUnknownMemberType]
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=render_as_batch,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config: Any = context.config
    target_metadata = config.attributes.get("target_metadata")
    render_as_batch = config.get_main_option("hearth.render_as_batch") == "true"
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(  # pyright: ignore[reportUnknownMemberType]
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=render_as_batch,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
