"""Auto-form field derivation from Pydantic Action schemas."""

from __future__ import annotations

import logging
import types as _types
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Union, get_args, get_origin

from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen, MultipleOf
from pydantic import AnyUrl, HttpUrl, SecretStr
from pydantic_core import PydanticUndefined

from hearth.identifiers import EntityId
from hearth.primitives.action import Action

logger = logging.getLogger("hearth_web")


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    input_type: str
    required: bool
    default: Any
    description: str | None
    choices: list[str] | None
    attrs: dict[str, str]


def _humanize(name: str) -> str:
    return name.replace("_", " ").title()


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    """Strip Optional / X | None. Returns (inner_type, was_optional)."""
    origin = get_origin(tp)
    args = get_args(tp)
    is_union = origin is Union or (
        hasattr(_types, "UnionType") and isinstance(tp, _types.UnionType)
    )
    if is_union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
        return tp, False
    return tp, False


def _to_input_type(tp: Any) -> tuple[str, list[str] | None]:
    """Return (html_input_type, choices_or_None)."""
    origin = get_origin(tp)
    args = get_args(tp)

    # Union already unwrapped by caller; handle list[str]
    if origin is list:
        inner = args[0] if args else str
        if inner is not str:
            logger.warning("autoform: complex list type %s falls back to text", tp)
        return "text", None

    if not isinstance(tp, type):
        logger.warning("autoform: unknown type %s falls back to text", tp)  # pyright: ignore[reportUnknownArgumentType]
        return "text", None

    # Order matters: bool is a subclass of int
    if issubclass(tp, bool):
        return "checkbox", None
    if issubclass(tp, Enum):
        return "select", [e.value for e in tp]  # type: ignore[misc]
    if issubclass(tp, SecretStr):
        return "password", None
    if issubclass(tp, EntityId):
        return "text", None
    if issubclass(tp, datetime):
        return "datetime-local", None
    if issubclass(tp, date):
        return "date", None
    if issubclass(tp, str):
        # Check for pydantic EmailStr / AnyUrl at type identity level
        if tp.__name__ in ("EmailStr",):
            return "email", None
        return "text", None
    if issubclass(tp, int):
        return "number", None
    if issubclass(tp, (float, Decimal)):
        return "number", None
    if tp in (AnyUrl, HttpUrl) or (
        hasattr(tp, "__name__") and tp.__name__ in ("AnyUrl", "HttpUrl")  # pyright: ignore[reportUnknownArgumentType]
    ):
        return "url", None

    logger.warning("autoform: unknown type %s falls back to text", tp)  # pyright: ignore[reportUnknownArgumentType]
    return "text", None


def _constraint_attrs(metadata: list[Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in metadata:
        if isinstance(m, MinLen):
            attrs["minlength"] = str(m.min_length)
        elif isinstance(m, MaxLen):
            attrs["maxlength"] = str(m.max_length)
        elif isinstance(m, Ge):
            attrs["min"] = str(m.ge)
        elif isinstance(m, Gt):
            attrs["min"] = str(m.gt)
        elif isinstance(m, Le):
            attrs["max"] = str(m.le)
        elif isinstance(m, Lt):
            attrs["max"] = str(m.lt)
        elif isinstance(m, MultipleOf):
            attrs["step"] = str(m.multiple_of)
        elif hasattr(m, "pattern") and isinstance(getattr(m, "pattern", None), str):
            attrs["pattern"] = m.pattern
    return attrs


def fields_for_action(
    action_cls: type[Action],
    include: list[str] | None = None,
) -> list[FormField]:
    """Derive a list of FormField from an Action's Pydantic schema.

    Args:
        action_cls: The Action subclass to inspect.
        include: If given, only return fields whose names are in this list
                 (preserving declaration order from action_cls.model_fields).
    """
    result: list[FormField] = []
    for name, field_info in action_cls.model_fields.items():
        if include is not None and name not in include:
            continue

        raw_type = field_info.annotation
        if raw_type is None:
            continue

        inner_type, was_optional = _unwrap_optional(raw_type)
        input_type, choices = _to_input_type(inner_type)

        required = field_info.is_required() and not was_optional

        default = field_info.default
        if default is PydanticUndefined:
            default = None

        description = field_info.description

        attrs = _constraint_attrs(field_info.metadata)

        # list[str] gets a hint via description override
        if input_type == "text" and get_origin(inner_type) is list:
            description = description or "Enter values separated by commas"

        result.append(
            FormField(
                name=name,
                label=_humanize(name),
                input_type=input_type,
                required=required,
                default=default,
                description=description,
                choices=choices,
                attrs=attrs,
            )
        )
    return result
