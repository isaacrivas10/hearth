"""Plugin-author surface for Hearth migrations.

Plugin migration files import `OpSummary` from here. Everything else they
need (the `op` namespace, types) comes from Alembic and SQLAlchemy directly —
Hearth deliberately does not wrap those (see ADR-0006 walled-garden
amendment).
"""

from hearth.migrations._ops import OpSummary

__all__ = ["OpSummary"]
