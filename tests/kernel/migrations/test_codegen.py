"""Tests for the F2 post-processor — emitting HEARTH_UPGRADE_OPS into
freshly-generated revision files plus the commented data-hook stub."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic.operations import ops as aop

from hearth.kernel.migrations._codegen import (
    DATA_HOOK_STUB,
    emit_op_summary_lines,
    post_process_generated_file,
)


def test_emit_op_summary_lines_renders_safe_destructive() -> None:
    ops = [
        aop.CreateTableOp("users", [sa.Column("id", sa.BigInteger(), nullable=False)]),
        aop.DropColumnOp("users", "legacy"),
    ]
    out = emit_op_summary_lines(ops, "HEARTH_UPGRADE_OPS")
    assert "destructive=False" in out
    assert "destructive=True" in out
    # `repr()` on str picks single quotes by default; check both forms so the
    # test stays robust if Python ever switches default quote style.
    assert "kind='CreateTable'" in out or 'kind="CreateTable"' in out
    assert "kind='DropColumn'" in out or 'kind="DropColumn"' in out


def test_emit_op_summary_lines_escapes_special_chars() -> None:
    """Descriptions / kinds may contain double-quotes, backslashes, or
    newlines (e.g. `DROP TABLE "users"`, Windows-style paths). The emitter
    must round-trip them through a valid Python literal so the generated
    revision file parses cleanly.
    """
    op = aop.DropColumnOp('table "weird"', "col\\with\nnewline")
    # Force a description with a double-quote via the public path: monkeypatch
    # `_describe` to return our payload, since the helper builds a canonical
    # `KIND table.col` string by default.
    payloads = [
        'DROP TABLE "users"',
        "alter \\path\\with\\slashes",
        "two\nline\ndesc",
        "mixed 'single' and \"double\"",
    ]
    for payload in payloads:
        with patch("hearth.kernel.migrations._codegen._describe", return_value=payload):
            out = emit_op_summary_lines([op], "HEARTH_UPGRADE_OPS")
        # The whole snippet must be syntactically valid Python.
        module = ast.parse(out)
        assert module is not None
        # And it must compile — catches subtler issues like unterminated strings.
        compile(out, "<test>", "exec")
        # Round-trip: the parsed `desc=` literal must equal the original
        # payload. Without this, a future double-escape bug would still
        # produce parseable Python but mangle the string value.
        assign = module.body[0]
        # The emitter writes `HEARTH_UPGRADE_OPS: list[OpSummary] = [...]`,
        # an annotated assignment.
        assert isinstance(assign, ast.AnnAssign)
        list_node = assign.value
        assert isinstance(list_node, ast.List)
        first_call = list_node.elts[0]
        assert isinstance(first_call, ast.Call)
        desc_kw = next(k for k in first_call.keywords if k.arg == "desc")
        desc_literal = desc_kw.value
        assert isinstance(desc_literal, ast.Constant)
        assert desc_literal.value == payload


def test_post_process_inserts_lists_and_stub(tmp_path: Path) -> None:
    f = tmp_path / "0001_test.py"
    f.write_text(
        textwrap.dedent("""\
        \"\"\"test rev\"\"\"
        from alembic import op
        import sqlalchemy as sa

        revision = "0001"
        down_revision = None
        branch_labels = ("test",)
        depends_on = None


        def upgrade():
            op.create_table("x", sa.Column("id", sa.Integer, primary_key=True))


        def downgrade():
            op.drop_table("x")
    """)
    )
    post_process_generated_file(
        f,
        upgrade_ops=[aop.CreateTableOp("x", [sa.Column("id", sa.Integer(), primary_key=True)])],
        downgrade_ops=[aop.DropTableOp("x")],
    )
    out = f.read_text()
    assert "from hearth.migrations import OpSummary" in out
    assert "HEARTH_UPGRADE_OPS" in out
    assert "HEARTH_DOWNGRADE_OPS" in out
    assert DATA_HOOK_STUB.strip()[:30] in out
