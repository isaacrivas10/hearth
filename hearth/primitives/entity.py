"""Entity primitive — see docs/core/primitives/entity.md.

Plugin authors inherit from `Entity` and write plain type annotations.
The kernel's metaclass synthesises SQLAlchemy `mapped_column(...)` from
the annotations, so plugins never import from `sqlalchemy`.
"""

from __future__ import annotations

import sys
from typing import Any, ClassVar, cast, dataclass_transform, get_origin, get_type_hints

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import composite, mapped_column, relationship
from sqlalchemy.orm.decl_api import DCTransformDeclarative

from hearth.fields import (
    Field,
    _FieldMarker,  # pyright: ignore[reportPrivateUsage]
)
from hearth.fields import ForeignKey as _ForeignKeyField
from hearth.identifiers import EntityId
from hearth.kernel.persistence import (
    _MAPPED_COLUMN_TYPE,  # pyright: ignore[reportPrivateUsage]
    HearthBase,
    _is_composable_value,  # pyright: ignore[reportPrivateUsage]
    _make_composite_comparator_factory,  # pyright: ignore[reportPrivateUsage]
    _make_composite_factory,  # pyright: ignore[reportPrivateUsage]
    column_for_annotation,
    is_optional,
    strip_optional,
)
from hearth.references import (
    ON_DELETE_MAP,
    References,
    _ReferenceMarker,  # pyright: ignore[reportPrivateUsage]
)

_NAMESPACE_SKIP = frozenset({"metadata", "registry", "__tablename__"})


def _resolve_annotations(
    annotations: dict[str, Any],
    module_name: str | None,
) -> dict[str, Any]:
    """Resolve possibly-stringified annotations (PEP 563) using the defining
    module's globals. Uses `typing.get_type_hints` indirectly via a temp class."""
    if not annotations:
        return {}
    module = sys.modules.get(module_name) if module_name else None
    globalns = dict(vars(module)) if module is not None else None
    tmp_cls = type(
        "_HearthAnnotResolver",
        (),
        {"__annotations__": dict(annotations), "__module__": module_name or "__main__"},
    )
    try:
        return get_type_hints(tmp_cls, globalns=globalns, include_extras=True)
    except (NameError, AttributeError, TypeError):
        return dict(annotations)


class _EntityMeta(DCTransformDeclarative):
    """Metaclass that synthesises `mapped_column(...)` descriptors from plain
    type annotations BEFORE SQLAlchemy's dataclass/ORM machinery runs.

    Two-pass: (1) install columns for non-Entity annotations, (2) upgrade the
    FK column for each Entity-typed annotation and install the relationship
    descriptor.
    """

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        fk_column_names: set[str] = set()
        composite_subcolumns: set[str] = set()
        if not namespace.get("__abstract__", False):
            raw_annotations = namespace.get("__annotations__", {})
            resolved = _resolve_annotations(
                raw_annotations,
                namespace.get("__module__"),
            )
            plugin_for_self_ref = kwargs.get("plugin") or _inherit_plugin(bases)

            relationship_specs: list[tuple[str, Any, _ReferenceMarker]] = []

            for fname in list(raw_annotations):
                if fname.startswith("_") or fname in _NAMESPACE_SKIP:
                    continue
                annot = resolved.get(fname)
                if annot is None or get_origin(annot) is ClassVar:
                    continue
                existing = namespace.get(fname, ...)
                if isinstance(existing, _MAPPED_COLUMN_TYPE):
                    continue

                inner = strip_optional(annot)
                if isinstance(existing, _ReferenceMarker):
                    relationship_specs.append((fname, annot, existing))
                    continue
                if isinstance(inner, type) and issubclass(inner, Entity):
                    relationship_specs.append((fname, annot, _ReferenceMarker(inner)))
                    continue
                if (
                    isinstance(inner, type)
                    and issubclass(inner, BaseModel)
                    and _is_composable_value(inner)
                ):
                    composite_subcolumns.update(
                        _install_composite(namespace, fname, annot, inner),
                    )
                    continue

                col_type = column_for_annotation(annot)
                col_kwargs: dict[str, Any] = {"nullable": is_optional(annot)}
                if isinstance(existing, _FieldMarker):
                    col_kwargs.update(existing.kwargs)
                elif existing is not ...:
                    col_kwargs["default"] = existing
                namespace[fname] = mapped_column(col_type, **col_kwargs)

            for attr_name, annot, marker in relationship_specs:
                fk_name = _install_reference(
                    namespace,
                    resolved,
                    name,
                    plugin_for_self_ref,
                    attr_name,
                    annot,
                    marker,
                )
                fk_column_names.add(fk_name)
        namespace["_hearth_fk_columns"] = frozenset(fk_column_names)
        namespace["_hearth_composite_subcolumns"] = frozenset(composite_subcolumns)
        return super().__new__(mcs, name, bases, namespace, **kwargs)


def _annotation_is_optional(annot: Any) -> bool:
    if isinstance(annot, str):
        return annot.endswith("| None") or annot.endswith("|None") or "Optional[" in annot
    return is_optional(annot)


def _inherit_plugin(bases: tuple[type, ...]) -> str | None:
    for b in bases:
        plugin = getattr(b, "_hearth_plugin", None)
        if plugin is not None:
            return plugin
    return None


def _install_reference(
    namespace: dict[str, Any],
    resolved: dict[str, Any],
    cls_name: str,
    plugin: str | None,
    attr_name: str,
    annot: Any,
    marker: _ReferenceMarker,
) -> str:
    if attr_name.endswith("_id"):
        raise TypeError(
            f"References attribute {attr_name!r} must not end in '_id'; "
            f"use {attr_name[:-3]!r} for the relationship and {attr_name!r} for "
            f"the column.",
        )
    rel_nullable = _annotation_is_optional(annot)
    column_name = marker.column or f"{attr_name}_id"

    fk_annot = resolved.get(column_name)
    if fk_annot is None:
        raise TypeError(
            f"References on {attr_name!r}: missing FK column declaration "
            f"{column_name!r}. Declare `{column_name}: EntityId"
            f"{' | None' if rel_nullable else ''}` alongside the relationship.",
        )
    fk_inner = strip_optional(fk_annot)
    if fk_inner is not EntityId:
        raise TypeError(
            f"References on {attr_name!r}: FK column {column_name!r} must be "
            f"annotated `EntityId` or `EntityId | None`, got {fk_annot!r}.",
        )
    fk_nullable = is_optional(fk_annot)
    if rel_nullable != fk_nullable:
        rel_state = "nullable" if rel_nullable else "not nullable"
        fk_state = "nullable" if fk_nullable else "not nullable"
        raise TypeError(
            f"References on {attr_name!r}: nullability mismatch — relationship "
            f"{attr_name!r} is {rel_state} but FK column {column_name!r} is "
            f"{fk_state}. They must match.",
        )
    if marker.on_delete == "set_null" and not rel_nullable:
        raise TypeError(
            f"References on {attr_name!r} uses on_delete='set_null' but neither "
            f"{attr_name!r} nor {column_name!r} is Optional; mark both nullable.",
        )

    target = marker.target
    if isinstance(target, str):
        if target != "Self" and target != cls_name:
            raise TypeError(
                f"References on {attr_name!r}: string target {target!r} is only "
                f"supported for self-references ('Self' or the class's own name). "
                f"Use the class object for cross-entity references.",
            )
        base = cls_name.lower()
        target_table = f"{plugin}__{base}" if plugin else base
        rel_target: Any = cls_name
    else:
        target_table = target.__tablename__
        rel_target = target

    # FK columns must be init=False, default=None: SA populates them from the
    # relationship at flush time. Without this, plugin authors would have to
    # pass customer_id explicitly even when they're already passing customer.
    fk_col = mapped_column(
        String,
        ForeignKey(
            f"{target_table}.id",
            ondelete=ON_DELETE_MAP[marker.on_delete],
        ),
        nullable=fk_nullable,
        init=False,
        default=None,
    )
    namespace[column_name] = fk_col

    namespace[attr_name] = relationship(
        rel_target,
        lazy="raise",
        foreign_keys=[fk_col],
        default=None,
    )
    return column_name


def _install_composite(
    namespace: dict[str, Any],
    attr_name: str,
    annot: Any,
    value_cls: type[BaseModel],
) -> list[str]:
    nullable = is_optional(annot)
    field_names = list(value_cls.model_fields.keys())
    sub_cols: list[Any] = []
    sub_col_names: list[str] = []
    annotations = namespace["__annotations__"]
    # Drop the composite annotation so we can re-insert it after the sub-columns.
    # Dataclass __init__ assigns fields in annotation order, and the composite
    # descriptor's fset decomposes Money into sub-columns. If sub-columns are
    # listed AFTER the composite, their default=None overwrites the decomposed
    # values; sub-columns must come first.
    composite_annot = annotations.pop(attr_name)
    for fname in field_names:
        # _is_composable_value already verified every field has a non-None annotation
        # that column_for_annotation can dispatch.
        field_annot = cast("Any", value_cls.model_fields[fname].annotation)
        col_type = column_for_annotation(field_annot)
        col_name = f"{attr_name}_{fname}"
        col = mapped_column(
            col_name,
            col_type,
            nullable=nullable or is_optional(field_annot),
            init=False,
            default=None,
        )
        namespace[col_name] = col
        annotations[col_name] = field_annot | None
        sub_cols.append(col)
        sub_col_names.append(col_name)
    annotations[attr_name] = composite_annot

    factory = _make_composite_factory(value_cls, field_names)
    comparator_factory = _make_composite_comparator_factory(field_names)
    if nullable:
        namespace[attr_name] = composite(
            factory,
            *sub_cols,
            comparator_factory=comparator_factory,
            default=None,
        )
    else:
        namespace[attr_name] = composite(
            factory,
            *sub_cols,
            comparator_factory=comparator_factory,
        )
    return sub_col_names


def _is_entity_annotation(annot: Any) -> bool:
    inner = strip_optional(annot)
    return isinstance(inner, type) and issubclass(inner, Entity)


@dataclass_transform(
    kw_only_default=True,
    eq_default=False,
    field_specifiers=(Field, _ForeignKeyField, mapped_column, References, relationship),
)
class Entity(HearthBase, metaclass=_EntityMeta, kw_only=True, eq=False):
    __abstract__ = True
    _hearth_plugin: ClassVar[str | None] = None
    _hearth_fk_columns: ClassVar[frozenset[str]] = frozenset()
    _hearth_composite_subcolumns: ClassVar[frozenset[str]] = frozenset()

    # Kernel-assigned identity. ULID-backed, generated at construction so
    # plugin code can reference customer.id immediately (e.g., for emit).
    id: EntityId = cast(
        "EntityId",
        mapped_column(
            String,
            primary_key=True,
            init=False,
            default_factory=EntityId.new,
        ),
    )

    def __init_subclass__(cls, *, plugin: str | None = None, **kwargs: Any) -> None:
        if plugin is not None:
            cls._hearth_plugin = plugin
        if not cls.__dict__.get("__abstract__", False) and "__tablename__" not in cls.__dict__:
            base = cls.__name__.lower()
            cls.__tablename__ = f"{cls._hearth_plugin}__{base}" if cls._hearth_plugin else base
        super().__init_subclass__(**kwargs)

    def __post_init__(self) -> None:
        cls = type(self)
        try:
            resolved = get_type_hints(cls, include_extras=True)
        except (NameError, AttributeError, TypeError):
            resolved = {}
        for fname, annot in resolved.items():
            if fname.startswith("_") or fname in _NAMESPACE_SKIP:
                continue
            if get_origin(annot) is ClassVar:
                continue
            if _is_entity_annotation(annot):
                continue
            if fname in cls._hearth_composite_subcolumns:
                continue
            try:
                value = getattr(self, fname)
            except AttributeError:
                continue
            if value is None and (is_optional(annot) or fname in cls._hearth_fk_columns):
                continue
            TypeAdapter(annot).validate_python(value)
        self.validate()

    def validate(self) -> None:
        """Plugin override hook for cross-field invariants. Raise to reject."""

    def __setattr__(self, name: str, value: Any) -> None:
        # Validate user-driven mutations against the field's annotation.
        # SQLAlchemy populates instances via descriptor protocol bypassing
        # this path on ORM load, so this only catches plugin-author code paths.
        if (
            not name.startswith("_")
            and name not in _NAMESPACE_SKIP
            and "_sa_instance_state" in self.__dict__
        ):
            try:
                resolved = get_type_hints(type(self), include_extras=True)
                annot = resolved.get(name)
            except (NameError, AttributeError, TypeError):
                annot = None
            if (
                annot is not None
                and not _is_entity_annotation(annot)
                and name not in type(self)._hearth_composite_subcolumns
                and not (value is None and is_optional(annot))
                and not (value is None and name in type(self)._hearth_fk_columns)
            ):
                TypeAdapter(annot).validate_python(value)
        super().__setattr__(name, value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return NotImplemented
        if type(self) is not type(other):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))
