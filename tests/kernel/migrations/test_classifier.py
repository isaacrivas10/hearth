"""Tests for the destructive-op classifier."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.operations import ops as aop

from hearth.kernel.migrations.classifier import classify


def _col(
    name: str,
    type_: sa.types.TypeEngine,
    nullable: bool = True,
    server_default=None,
) -> sa.Column:
    return sa.Column(name, type_, nullable=nullable, server_default=server_default)


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        (aop.CreateTableOp("user", [_col("id", sa.BigInteger(), nullable=False)]), "safe"),
        (aop.DropTableOp("user"), "destructive"),
        (aop.AddColumnOp("user", _col("phone", sa.String(), nullable=True)), "safe"),
        (
            aop.AddColumnOp(
                "user",
                _col("phone", sa.String(), nullable=False, server_default="''"),
            ),
            "safe",
        ),
        (aop.AddColumnOp("user", _col("phone", sa.String(), nullable=False)), "destructive"),
        (aop.DropColumnOp("user", "phone"), "destructive"),
        (aop.CreateIndexOp("ix_user_email", "user", ["email"]), "safe"),
        (aop.DropIndexOp("ix_user_email", "user", unique=True), "destructive"),
        (aop.DropIndexOp("ix_user_email", "user", unique=False), "safe"),
        (aop.CreateUniqueConstraintOp("uq_user_email", "user", ["email"]), "destructive"),
        (aop.CreateCheckConstraintOp("ck_user_age", "user", "age >= 0"), "destructive"),
        (aop.DropConstraintOp("uq_user_email", "user"), "destructive"),
        (aop.ExecuteSQLOp("UPDATE user SET phone = NULL"), "destructive"),
    ],
)
def test_classify_static_cases(op, expected) -> None:
    assert classify(op) == expected


def test_classify_alter_column_rename_is_safe() -> None:
    op = aop.AlterColumnOp("user", "full_name", modify_name="display_name")
    assert classify(op) == "safe"


def test_classify_alter_column_nullable_to_false_is_destructive() -> None:
    op = aop.AlterColumnOp("user", "email", modify_nullable=False, existing_nullable=True)
    assert classify(op) == "destructive"


def test_classify_alter_column_nullable_to_true_is_safe() -> None:
    op = aop.AlterColumnOp("user", "email", modify_nullable=True, existing_nullable=False)
    assert classify(op) == "safe"


def test_classify_alter_column_type_widen_is_safe() -> None:
    op = aop.AlterColumnOp(
        "user", "age", modify_type=sa.BigInteger(), existing_type=sa.Integer()
    )
    assert classify(op) == "safe"


def test_classify_alter_column_type_narrow_is_destructive() -> None:
    op = aop.AlterColumnOp(
        "user", "age", modify_type=sa.Integer(), existing_type=sa.BigInteger()
    )
    assert classify(op) == "destructive"


def test_classify_unknown_op_is_destructive() -> None:
    """Unknown ops fail closed — operator can always say yes, we cannot un-drop."""

    class _MysteryOp:
        pass

    assert classify(_MysteryOp()) == "destructive"
