"""Registry -> view-model helpers for the admin console (read-only)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Column

from hearth.kernel.registry import Registry


def _col_type_name(col: Column[Any]) -> str:
    """Extract a clean type name from a SQLAlchemy column type."""
    # Use the visit name if available (e.g., "INTEGER", "VARCHAR", "DATETIME")
    if hasattr(col.type, "__visit_name__") and col.type.__visit_name__:
        return col.type.__visit_name__.upper()
    # Fallback: string representation without length/precision
    return str(col.type).split("(")[0].upper()


def counts(registry: Registry) -> dict[str, int]:
    plugins = registry.plugins.values()
    return {
        "plugins": len(registry.plugins),
        "entities": sum(len(p.entities) for p in plugins),
        "actions": sum(len(p.actions) for p in plugins),
        "events": sum(len(p.events) for p in plugins),
    }


def entity_fields(entity_cls: type) -> list[dict[str, Any]]:
    """Columns + types + FK targets from the mapped table."""
    table = getattr(entity_cls, "__table__", None)
    if table is None:
        return []
    out: list[dict[str, Any]] = []
    for col in table.columns:
        fks = [fk.target_fullname for fk in col.foreign_keys]
        out.append(
            {
                "name": col.name,
                "type": _col_type_name(col),
                "nullable": col.nullable,
                "references": fks,
            }
        )
    return out


def schema_graph(registry: Registry) -> dict:
    """Build Cytoscape.js graph data from entity tables + FKs."""
    nodes = []
    edges = []
    for info in registry.plugins.values():
        for ent in info.entities:
            table = getattr(ent, "__table__", None)
            if table is None:
                continue
            columns = [
                {"name": col.name, "type": _col_type_name(col)}
                for col in table.columns
            ]
            nodes.append({
                "data": {
                    "id": table.name,
                    "label": table.name,
                    "columns": columns,
                }
            })
            for col in table.columns:
                for fk in col.foreign_keys:
                    target = fk.column.table.name
                    edge_id = f"{table.name}__{col.name}__{target}"
                    edges.append({
                        "data": {
                            "id": edge_id,
                            "source": table.name,
                            "target": target,
                            "label": col.name,
                        }
                    })
    return {"nodes": nodes, "edges": edges}


async def db_status(engine: Any, registry: Registry) -> dict[str, Any]:
    """Live DB health: which expected tables exist + outbox row count.

    Kernel tables (`_hearth_outbox`, `_hearth_schema_log`) plus every
    registered entity's table are checked against the live schema. `outbox_depth`
    is the number of rows currently in the outbox table (0 if absent).
    """
    from sqlalchemy import func, inspect, select
    from sqlalchemy.engine import Connection

    from hearth.kernel.migrations.audit_log import SCHEMA_LOG_TABLE
    from hearth.kernel.persistence import OUTBOX_TABLE

    kernel_table_names = [OUTBOX_TABLE.name, SCHEMA_LOG_TABLE.name]
    entity_table_names: list[str] = []
    for info in registry.plugins.values():
        for ent in info.entities:
            table = getattr(ent, "__table__", None)
            if table is not None:
                entity_table_names.append(table.name)

    async with engine.connect() as conn:
        from sqlalchemy.engine import Connection

        def get_tables(conn: Connection) -> list[str]:
            return inspect(conn).get_table_names()

        existing = set(await conn.run_sync(get_tables))
        outbox_depth = 0
        if OUTBOX_TABLE.name in existing:
            result = await conn.execute(select(func.count()).select_from(OUTBOX_TABLE))
            outbox_depth = int(result.scalar_one())

    tables = [
        {"name": name, "present": name in existing}
        for name in (*kernel_table_names, *entity_table_names)
    ]
    kernel_ok = all(name in existing for name in kernel_table_names)
    return {"tables": tables, "outbox_depth": outbox_depth, "kernel_ok": kernel_ok}
