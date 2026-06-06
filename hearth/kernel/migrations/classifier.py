"""Destructive-op classifier: inspects an Alembic `MigrateOperation` and
returns the verdict that drives interactive confirmation at apply time.

Runs at *generate* time (where the Alembic op instances exist with their
fields) and persists into the revision file as `HEARTH_UPGRADE_OPS`. See
[docs/superpowers/specs/2026-05-18-schema-migrations-design.md].
"""

from __future__ import annotations

from typing import Any, Literal

import sqlalchemy as sa
from alembic.operations import ops as aop

Classification = Literal["safe", "destructive"]


# Type families that contain each other losslessly. (int → bigint widens; the
# reverse narrows.) This is a conservative table; the slice may refine.
_NUMERIC_WIDTH: dict[type[sa.types.TypeEngine[Any]], int] = {
    sa.SmallInteger: 1,
    sa.Integer: 2,
    sa.BigInteger: 3,
}


def _is_lossless_type_change(
    existing: sa.types.TypeEngine[Any], modify: sa.types.TypeEngine[Any]
) -> bool:
    e_cls, m_cls = type(existing), type(modify)
    if e_cls is m_cls:
        # Same family — check length if applicable.
        e_len = getattr(existing, "length", None)
        m_len = getattr(modify, "length", None)
        if e_len is not None and m_len is not None:
            return m_len >= e_len
        return True
    # Numeric widening
    e_width = _NUMERIC_WIDTH.get(e_cls)
    m_width = _NUMERIC_WIDTH.get(m_cls)
    if e_width is not None and m_width is not None:
        return m_width >= e_width
    return False


def classify(op: object) -> Classification:
    """Return the verdict for a single Alembic `MigrateOperation`.

    Errs toward 'destructive' for any op the classifier does not recognize —
    the operator can say yes interactively; we cannot un-drop a column they
    did not know was being dropped.
    """
    match op:
        case aop.CreateTableOp():
            return "safe"
        case aop.DropTableOp():
            return "destructive"
        case aop.AddColumnOp():
            col = op.column
            if col.nullable:
                return "safe"
            if col.server_default is not None:
                return "safe"
            return "destructive"
        case aop.DropColumnOp():
            return "destructive"
        case aop.AlterColumnOp():
            if op.modify_name is not None:
                return "safe"
            if op.modify_nullable is False and op.existing_nullable is not False:
                return "destructive"
            if op.modify_nullable is True:
                return "safe"
            if op.modify_type is not None and op.existing_type is not None:
                if _is_lossless_type_change(op.existing_type, op.modify_type):
                    return "safe"
                return "destructive"
            return "safe"
        case aop.RenameTableOp():
            return "safe"
        case aop.CreateIndexOp():
            return "safe"
        case aop.DropIndexOp():
            return "destructive" if op.kw.get("unique", False) else "safe"
        case aop.CreateUniqueConstraintOp() | aop.CreateCheckConstraintOp():
            return "destructive"
        case aop.CreateForeignKeyOp():
            return "safe"
        case aop.DropConstraintOp():
            return "destructive"
        case aop.ExecuteSQLOp():
            return "destructive"
        case _:
            return "destructive"
