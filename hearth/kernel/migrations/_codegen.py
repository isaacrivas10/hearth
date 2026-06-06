"""Code emitters for `hearth migrate generate` post-processing.

The post-processor runs after Alembic writes a revision file. It walks
`MigrationScript.upgrade_ops` (available during generation), classifies
each op, and inserts a HEARTH_UPGRADE_OPS / HEARTH_DOWNGRADE_OPS module
constant near the top. It also appends the commented data-hook stub.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from alembic.operations.ops import MigrateOperation

from hearth.kernel.migrations.classifier import classify


def emit_op_summary_lines(ops: Iterable[MigrateOperation], variable_name: str) -> str:
    """Render `HEARTH_UPGRADE_OPS = [OpSummary(...), ...]`."""
    lines = [f"{variable_name}: list[OpSummary] = ["]
    for op in ops:
        kind = type(op).__name__.removesuffix("Op")
        desc = _describe(op)
        destructive = classify(op) == "destructive"
        # Use `!r` (repr) so descriptions / kinds that contain quotes,
        # backslashes, or newlines (e.g. `DROP TABLE "users"`) stay valid
        # Python literals in the generated revision file.
        lines.append(
            f"    OpSummary(kind={kind!r}, desc={desc!r}, destructive={destructive}),"
        )
    lines.append("]")
    return "\n".join(lines)


def _describe(op: MigrateOperation) -> str:
    """Human description for plan output and audit log."""
    kind = type(op).__name__.removesuffix("Op").upper()
    table = getattr(op, "table_name", None) or getattr(op, "name", "")
    column = getattr(op, "column_name", None) or getattr(
        getattr(op, "column", None), "name", ""
    )
    if column:
        return f"{kind} {table}.{column}".strip()
    return f"{kind} {table}".strip()


DATA_HOOK_STUB = '''
# Optional: define data_upgrade / data_downgrade to seed or migrate data
# using the kernel's UnitOfWork. The kernel runs them in a UoW scoped to
# PluginActor("<plugin>"). Event emission is disabled for the data UoW.
#
# IMPORTANT — upgrade() commits BEFORE data_upgrade runs. If data_upgrade
# raises, Alembic's branch head is already advanced; a re-run will call
# data_upgrade again (via the recovery check) without re-running upgrade().
# Write your function to be safe on repeated calls — find-then-save, not
# unconditional insert.
#
# IMPORTANT — if you add data_upgrade, also append an OpSummary(kind=
# "DataMigration", desc=..., destructive=False) entry to HEARTH_UPGRADE_OPS
# above. `hearth migrate check` will fail otherwise: operators reading
# `hearth db plan` need to see this side effect before approving the plan.
#
# async def data_upgrade(uow) -> None:
#     ...
#
# async def data_downgrade(uow) -> None:
#     ...
'''


def post_process_generated_file(
    file_path: Path,
    upgrade_ops: Iterable[MigrateOperation],
    downgrade_ops: Iterable[MigrateOperation],
) -> None:
    """Insert HEARTH_UPGRADE_OPS / HEARTH_DOWNGRADE_OPS and the data stub."""
    content = file_path.read_text()

    # Insert the OpSummary import + the two lists after `depends_on = ...`
    import_line = "from hearth.migrations import OpSummary\n"
    op_lists = (
        "\n\n"
        + emit_op_summary_lines(upgrade_ops, "HEARTH_UPGRADE_OPS")
        + "\n"
        + emit_op_summary_lines(downgrade_ops, "HEARTH_DOWNGRADE_OPS")
        + "\n"
    )
    content = content.replace(
        "from alembic import op",
        "from alembic import op\n" + import_line,
        1,
    )
    # Insert op lists after depends_on assignment line.
    content = _insert_after_depends_on(content, op_lists)
    content += DATA_HOOK_STUB
    file_path.write_text(content)


def _insert_after_depends_on(content: str, snippet: str) -> str:
    """Find the line starting with 'depends_on' and insert `snippet` after it."""
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("depends_on"):
            return "".join(lines[: i + 1]) + snippet + "".join(lines[i + 1 :])
    # Fallback: append at end of imports
    return content + snippet
