# Spec: plugin and primitive registry

> **Source module**: `hearth/kernel/registry.py`
> **Parent ADRs**: [ADR-0003 (plugin model)](../../architecture/0003-plugin-model.md), [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).

## Status

Stub.

## Brief

The startup-time registry that discovers installed plugins (via Python
distribution metadata / entry points), validates their declared hard
dependencies, and indexes the entities, actions, events, jobs, and
views each plugin contributes.

The registry is the source of truth for "what plugins are running" at
runtime. Per ADR-0008, what's installed (via pip extras) is what's
active — there is no separate enable/disable step.

## Open questions

- **Discovery mechanism.** Entry points, distribution metadata,
  explicit manifest, or all of the above?
- **Values-only plugins.** Whether `commons` (a values-only plugin)
  needs a `Plugin` registration object at all. Deferred per ADR-0008
  open questions.
- **Dependency-graph validation at startup.** Missing plugin, version
  mismatch, circular hard dependencies — all errors at startup, not at
  request time.
- **Hot-reload posture.** Explicitly forbidden per ADR-0003; the
  registry refuses to mutate after startup.
- **Registry lookup latency.** Constant-time map vs. computed views.
