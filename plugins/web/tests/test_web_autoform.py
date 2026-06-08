"""Tests for auto-form field derivation from Pydantic Action schemas."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated

import pytest
from pydantic import Field, SecretStr

from hearth.identifiers import EntityId
from hearth.primitives.action import Action


def _fields(action_cls: type[Action], include=None):
    from hearth_web.admin.autoform import fields_for_action

    return {f.name: f for f in fields_for_action(action_cls, include=include)}


# ── type mapping ──────────────────────────────────────────────────────────────


def test_str_field():
    class A(Action):
        name: str

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["name"]
    assert f.input_type == "text"
    assert f.required is True


def test_int_field():
    class A(Action):
        count: int

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["count"]
    assert f.input_type == "number"


def test_bool_field():
    class A(Action):
        active: bool

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["active"]
    assert f.input_type == "checkbox"


def test_secret_str_field():
    class A(Action):
        password: SecretStr

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["password"]
    assert f.input_type == "password"


def test_enum_field():
    class Color(Enum):
        red = "red"
        blue = "blue"

    class A(Action):
        color: Color

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["color"]
    assert f.input_type == "select"
    assert f.choices == ["red", "blue"]


def test_optional_field_is_not_required():
    class A(Action):
        note: str | None = None

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["note"]
    assert f.required is False
    assert f.input_type == "text"


def test_entity_id_field():
    class A(Action):
        entity_id: EntityId

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["entity_id"]
    assert f.input_type == "text"


# ── constraint mapping ────────────────────────────────────────────────────────


def test_min_max_len_constraints():
    class A(Action):
        name: Annotated[str, Field(min_length=2, max_length=50)]

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["name"]
    assert f.attrs.get("minlength") == "2"
    assert f.attrs.get("maxlength") == "50"


def test_ge_le_constraints():
    class A(Action):
        score: Annotated[int, Field(ge=0, le=100)]

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["score"]
    assert f.attrs.get("min") == "0"
    assert f.attrs.get("max") == "100"


def test_multiple_of_constraint():
    class A(Action):
        qty: Annotated[int, Field(multiple_of=5)]

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["qty"]
    assert f.attrs.get("step") == "5"


# ── edge cases ────────────────────────────────────────────────────────────────


def test_zero_field_action_returns_empty_list():
    class A(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    from hearth_web.admin.autoform import fields_for_action

    assert fields_for_action(A) == []


def test_include_filter_limits_fields():
    class A(Action):
        name: str
        email: str

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    result = _fields(A, include=["name"])
    assert "name" in result
    assert "email" not in result


def test_label_humanized():
    class A(Action):
        first_name: str

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["first_name"]
    assert f.label == "First Name"


def test_description_from_field_info():
    class A(Action):
        code: Annotated[str, Field(description="The promo code")]

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    f = _fields(A)["code"]
    assert f.description == "The promo code"
