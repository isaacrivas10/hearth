"""Tests for the hearth CLI."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from hearth.cli import app


def test_version_flag_prints_kernel_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "hearth" in result.stdout.lower()


def test_no_args_prints_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, [])
    # Click 8.3's no_args_is_help on a group with a callback exits with code 2
    # (usage error), not 0. Accept either to stay forward-compatible.
    assert result.exit_code in (0, 2)
    combined = result.stdout + (result.stderr or "")
    assert "Usage" in combined


def test_unexpected_exception_renders_clean_error_without_debug(monkeypatch) -> None:
    from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]

    test_app = typer.Typer()

    @test_app.callback()
    def _cb() -> None:
        """Force subcommand mode so the single command isn't flattened."""

    @test_app.command()
    @_wrap_command
    def boom() -> None:
        raise RuntimeError("ouchies")

    monkeypatch.delenv("HEARTH_DEBUG", raising=False)
    runner = CliRunner()
    result = runner.invoke(test_app, ["boom"], catch_exceptions=True)
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "ERROR: unexpected" in combined
    assert "RuntimeError" in combined
    assert "ouchies" in combined


def test_unexpected_exception_reraises_with_debug_env(monkeypatch) -> None:
    from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]

    test_app = typer.Typer()

    @test_app.callback()
    def _cb() -> None:
        """Force subcommand mode so the single command isn't flattened."""

    @test_app.command()
    @_wrap_command
    def boom_debug() -> None:
        raise RuntimeError("ouchies-debug")

    monkeypatch.setenv("HEARTH_DEBUG", "1")
    runner = CliRunner()
    result = runner.invoke(test_app, ["boom-debug"], catch_exceptions=True)
    assert result.exit_code != 0
    assert result.exception is not None
    assert isinstance(result.exception, RuntimeError)


def test_plugins_list_zero_plugins(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])
    assert result.exit_code == 0
    assert "Installed plugins (0)" in result.stdout
    assert "(none)" in result.stdout


def test_plugins_list_renders_columns(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import bases_for

    mod = types.ModuleType("hearth_test_cli_list")
    sys.modules["hearth_test_cli_list"] = mod
    EBase, _, _ = bases_for("clitestlist")  # noqa: N806

    class Cust(EBase):
        name: str

    Cust.__module__ = "hearth_test_cli_list"
    mod.Cust = Cust

    class FakeEP:
        name = "clitestlist"
        value = "hearth_test_cli_list"
        dist = type(
            "FakeDist",
            (),
            {
                "name": "pkg-clitestlist",
                "version": "1.2.3",
                "requires": (),
            },
        )()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])
    assert result.exit_code == 0
    assert "clitestlist" in result.stdout
    assert "1.2.3" in result.stdout
    assert "hearth_test_cli_list" in result.stdout


def test_plugins_list_surfaces_registry_build_errors(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    class BrokenEP:
        name = "broken"
        value = "this_module_does_not_exist_xyz"
        dist = type("FakeDist", (), {"name": "pkg-broken", "version": "0", "requires": ()})()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [BrokenEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "ERROR" in combined
    assert "broken" in combined


def test_plugins_deps_renders_tree(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import EntityId, ForeignKey, References, bases_for

    mod_a = types.ModuleType("hearth_test_cli_deps_a")
    sys.modules["hearth_test_cli_deps_a"] = mod_a
    EA, _, _ = bases_for("citdeps_a")  # noqa: N806

    class Cust(EA):
        __module__ = "hearth_test_cli_deps_a"
        name: str

    mod_a.Cust = Cust  # type: ignore[attr-defined]

    mod_b = types.ModuleType("hearth_test_cli_deps_b")
    sys.modules["hearth_test_cli_deps_b"] = mod_b
    EB, _, _ = bases_for("citdeps_b")  # noqa: N806
    # Expose EntityId and Cust in the fake module's namespace so PEP-563
    # stringified annotations resolve when SQLAlchemy maps the entity.
    mod_b.EntityId = EntityId  # type: ignore[attr-defined]
    mod_b.Cust = Cust  # type: ignore[attr-defined]

    class Order(EB):
        __module__ = "hearth_test_cli_deps_b"
        customer_id: EntityId = ForeignKey()
        customer: Cust = References(Cust)

    mod_b.Order = Order  # type: ignore[attr-defined]

    def make_dist(name, version, requires):
        return type("FakeDist", (), {"name": name, "version": version, "requires": requires})()

    eps = [
        type(
            "FakeEP",
            (),
            {
                "name": "citdeps_a",
                "value": "hearth_test_cli_deps_a",
                "dist": make_dist("pkg-citdeps-a", "0", ()),
            },
        )(),
        type(
            "FakeEP",
            (),
            {
                "name": "citdeps_b",
                "value": "hearth_test_cli_deps_b",
                "dist": make_dist("pkg-citdeps-b", "0", ("pkg-citdeps-a",)),
            },
        )(),
    ]
    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: eps if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "deps"])
    assert result.exit_code == 0
    out = result.stdout
    assert "citdeps_a" in out
    assert "citdeps_b" in out
    assert "extends" in out


def test_plugins_deps_no_plugins(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "deps"])
    assert result.exit_code == 0
    assert "(no plugins installed)" in result.stdout or "(none)" in result.stdout


def test_plugins_show_renders_detail(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import bases_for

    mod = types.ModuleType("hearth_test_cli_show")
    sys.modules["hearth_test_cli_show"] = mod
    E, A, Ev = bases_for("citshow")  # noqa: N806

    class Cust(E):
        name: str

    class DoSomething(A):
        x: int

    class Happened(Ev):
        y: str

    for cls in (Cust, DoSomething, Happened):
        cls.__module__ = "hearth_test_cli_show"
        setattr(mod, cls.__name__, cls)

    class FakeEP:
        name = "citshow"
        value = "hearth_test_cli_show"
        dist = type("FakeDist", (), {"name": "pkg-citshow", "version": "9.9.9", "requires": ()})()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "show", "citshow"])
    assert result.exit_code == 0
    assert "citshow" in result.stdout
    assert "9.9.9" in result.stdout
    assert "Cust" in result.stdout
    assert "DoSomething" in result.stdout
    assert "Happened" in result.stdout


def test_plugins_show_unknown_alias_exits_1(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "show", "ghost"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "ghost" in combined


def test_db_init_missing_database_url_exits_1(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert "DATABASE_URL" in combined


def test_db_init_rejects_in_memory_sqlite(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert ":memory:" in combined


def test_db_init_creates_outbox(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hearth.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0
    combined = result.stdout + (result.stderr or "")
    assert "_hearth_outbox" in combined

    result2 = runner.invoke(app, ["db", "init"])
    assert result2.exit_code == 0


def test_db_status_reports_connection_ok(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hearth.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "status"])
    assert result.exit_code == 0
    assert "Connection" in result.stdout
    assert "OK" in result.stdout
    assert "_hearth_outbox" in result.stdout


def test_db_init_creates_plugin_tables(tmp_path, monkeypatch) -> None:
    """`hearth db init` must call Registry.build() so plugin entities register
    their tables in METADATA, then `create_all` picks them up."""
    import sqlite3
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import bases_for

    mod = types.ModuleType("hearth_test_db_init_plugin")
    sys.modules["hearth_test_db_init_plugin"] = mod
    EBase, _, _ = bases_for("dbinitplugin")  # noqa: N806

    class Widget(EBase):
        name: str

    Widget.__module__ = "hearth_test_db_init_plugin"
    mod.Widget = Widget

    class FakeEP:
        name = "dbinitplugin"
        value = "hearth_test_db_init_plugin"
        dist = type(
            "FakeDist",
            (),
            {
                "name": "pkg-dbinitplugin",
                "version": "0",
                "requires": (),
            },
        )()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    db_path = tmp_path / "hearth.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0

    con = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        con.close()

    assert "_hearth_outbox" in tables
    assert "dbinitplugin__widget" in tables, f"expected plugin table; got {tables}"
    # The new _init_impl iterates registry.plugins and echoes each plugin table.
    # Without Registry.build(), the CLI only echoes the kernel outbox, so this
    # asserts the observable behavior of the fix (not just the side effect on
    # the shared METADATA, which the test body itself populates).
    assert "dbinitplugin__widget" in result.stdout


def test_db_graph_text_output(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import EntityId, ForeignKey, References, bases_for

    mod = types.ModuleType("hearth_test_cli_graph")
    sys.modules["hearth_test_cli_graph"] = mod
    EBase, _, _ = bases_for("citgraph")  # noqa: N806
    # Expose EntityId and Customer in the fake module's namespace so PEP-563
    # stringified annotations resolve when SQLAlchemy maps the entity.
    mod.EntityId = EntityId  # type: ignore[attr-defined]

    class Customer(EBase):
        __module__ = "hearth_test_cli_graph"
        name: str

    mod.Customer = Customer  # type: ignore[attr-defined]

    class Order(EBase):
        __module__ = "hearth_test_cli_graph"
        customer_id: EntityId = ForeignKey()
        customer: Customer = References(Customer)

    mod.Order = Order  # type: ignore[attr-defined]

    class FakeEP:
        name = "citgraph"
        value = "hearth_test_cli_graph"
        dist = type("FakeDist", (), {"name": "pkg-citgraph", "version": "0", "requires": ()})()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["db", "graph"])
    assert result.exit_code == 0
    assert "citgraph__customer" in result.stdout
    assert "citgraph__order" in result.stdout
    assert "customer_id" in result.stdout


def test_plugin_cli_group_is_registered(monkeypatch) -> None:
    """A plugin contributing a Typer app via `hearth.cli` entry-point becomes
    available as `hearth <alias> ...`."""
    import importlib
    import sys
    import types

    fake_mod = types.ModuleType("hearth_test_cliext_demo")
    sub_app = typer.Typer(help="A test plugin CLI.")

    @sub_app.callback()
    def _root() -> None:
        """Forces subcommand mode."""

    @sub_app.command("hello")
    def _hello() -> None:
        typer.echo("hi from the plugin")

    fake_mod.sub_app = sub_app
    sys.modules["hearth_test_cliext_demo"] = fake_mod

    class FakeEP:
        name = "demoplugin"
        value = "hearth_test_cliext_demo:sub_app"

        def load(self):
            return sub_app

    monkeypatch.setattr(
        "hearth.cli.importlib.metadata.entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.cli" else [],
    )

    import hearth.cli

    importlib.reload(hearth.cli)
    from hearth.cli import app as reloaded_app

    runner = CliRunner()
    result = runner.invoke(reloaded_app, ["demoplugin", "hello"])
    assert result.exit_code == 0
    assert "hi from the plugin" in result.stdout


def test_plugin_cli_reserved_name_is_skipped(monkeypatch) -> None:
    """A plugin trying to register under a kernel-reserved CLI name (`plugins`
    or `db`) is skipped with a warning."""
    import importlib
    import sys
    import types

    fake_mod = types.ModuleType("hearth_test_cliext_clash")
    sub_app = typer.Typer()

    @sub_app.callback()
    def _root() -> None:
        """Forces subcommand mode."""

    @sub_app.command("naughty")
    def _naughty() -> None:
        typer.echo("should not see this")

    fake_mod.sub_app = sub_app
    sys.modules["hearth_test_cliext_clash"] = fake_mod

    class FakeEP:
        name = "plugins"  # reserved name
        value = "hearth_test_cliext_clash:sub_app"

        def load(self):
            return sub_app

    monkeypatch.setattr(
        "hearth.cli.importlib.metadata.entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.cli" else [],
    )

    import hearth.cli

    importlib.reload(hearth.cli)
    from hearth.cli import app as reloaded_app

    runner = CliRunner()
    result = runner.invoke(reloaded_app, ["plugins", "naughty"])
    # `plugins` still exists as the kernel-owned group; "naughty" is not its command.
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert "naughty" not in combined or "No such command" in combined


def test_plugin_cli_load_failure_does_not_crash(monkeypatch) -> None:
    """If a plugin's entry-point `ep.load()` raises, the CLI logs a warning
    but continues registering other plugins and remains usable."""
    import importlib

    class BrokenEP:
        name = "broken"
        value = "no.such.module:no_such_app"

        def load(self):
            raise ModuleNotFoundError("no.such.module")

    monkeypatch.setattr(
        "hearth.cli.importlib.metadata.entry_points",
        lambda **kw: [BrokenEP()] if kw.get("group") == "hearth.cli" else [],
    )

    import hearth.cli

    importlib.reload(hearth.cli)
    from hearth.cli import app as reloaded_app

    runner = CliRunner()
    # The bare CLI still works — broken plugin doesn't crash.
    result = runner.invoke(reloaded_app, ["--version"])
    assert result.exit_code == 0
