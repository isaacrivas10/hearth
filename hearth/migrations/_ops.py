"""Public dataclass plugin-author migration files import."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpSummary:
    """The classifier's verdict on a single migration op, persisted in
    revision files as `HEARTH_UPGRADE_OPS` / `HEARTH_DOWNGRADE_OPS` so the
    kernel can drive destructive-op confirmation at apply time without
    introspecting Alembic's opaque `upgrade()` body.
    """

    kind: str
    """Alembic op class name (e.g. 'AddColumn'), or 'DataMigration' for the
    Python `data_upgrade` / `data_downgrade` hooks."""

    desc: str
    """Human description used in plan output and audit log."""

    destructive: bool
    """The classifier's verdict. True triggers an interactive confirmation
    prompt before apply."""
