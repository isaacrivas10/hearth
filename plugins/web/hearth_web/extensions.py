"""Plugin web-contribution objects and discovery of the `hearth.web` group."""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("hearth_web")

RESERVED_NAMES = frozenset({"admin", "login", "logout", "static", "health", ""})

Provider = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class NavItem:
    label: str
    path: str
    permission: str | None = None
    section: str | None = None
    order: int = 0
    icon: str | None = None  # Reserved for future UI; not rendered in v1 templates


@dataclass(frozen=True)
class SlotContribution:
    slot: str
    template: str
    provider: Provider | None = None
    permission: str | None = None
    order: int = 0


@dataclass(frozen=True)
class WebModule:
    name: str
    router: APIRouter | None = None
    templates_dir: str | None = None
    static_dir: str | None = None
    package_dir: str | None = None
    nav: list[NavItem] = field(default_factory=list)
    contributions: list[SlotContribution] = field(default_factory=list)


EntryPoint = importlib.metadata.EntryPoint


def load_web_modules(entry_points: Iterable[EntryPoint]) -> list[WebModule]:
    """Resolve an iterable of entry points into WebModules, applying the same
    discipline as the CLI loader: reserved-name refusal, dedup, failure isolation."""
    registered: set[str] = set()
    modules: list[WebModule] = []
    for ep in entry_points:
        name = ep.name
        if name in RESERVED_NAMES:
            logger.warning("web module name %r is reserved; skipping", name)
            continue
        if name in registered:
            logger.warning("web module name %r already registered; skipping", name)
            continue
        try:
            module = ep.load()
        except Exception as exc:
            logger.warning("failed to load web module %r: %s: %s", name, type(exc).__name__, exc)
            continue
        if not isinstance(module, WebModule):
            logger.warning("web module %r did not resolve to a WebModule; skipping", name)
            continue
        modules.append(module)
        registered.add(name)
    return modules


def discover_web_modules() -> list[WebModule]:
    """Production entry point: discover the `hearth.web` group."""
    return load_web_modules(importlib.metadata.entry_points(group="hearth.web"))
