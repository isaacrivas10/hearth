# Built-in plugins

This directory holds plugins shipped in the Hearth monorepo. Per
[ADR-0008](../docs/architecture/0008-built-in-plugins.md), being
"built-in" carries no special runtime status — it just means the
plugin's source lives here for development convenience and is reachable
through pip extras at install time.

## How to install

```bash
pip install hearth                            # kernel only; no plugins active
pip install hearth[commons]                   # kernel + commons
pip install hearth[commons,commerce]          # kernel + commons + commerce
```

The kernel ships with **zero plugins active by default**. Every plugin
is opt-in. The operator's `pip install` command IS the plugin manifest;
there is no separate `plugins.toml` file.

## Layout

```
plugins/
├── README.md                # this file
├── commons/                 # values-only plugin: Money, PhoneNumber, Address, EmailAddress
│   ├── pyproject.toml
│   └── hearth_commons/
│       └── __init__.py
└── commerce/                # reference commerce plugin: Customer, Order, Product, ...
    ├── pyproject.toml
    └── hearth_commerce/
        ├── __init__.py
        ├── entities/
        ├── actions/
        ├── events/
        └── values.py        # commerce-specific values (HonduranID, RTN, etc.)
```

Each plugin is its own Python package with its own `pyproject.toml`. The
kernel's `pyproject.toml` declares each plugin as an extras target
(`hearth[commerce] -> hearth-commerce`).

## Where things live (the three-tier rule)

From [ADR-0008](../docs/architecture/0008-built-in-plugins.md):

| Where | Examples | Test |
| --- | --- | --- |
| Kernel | `EntityId`, `Reference` | The kernel manipulates the value as part of its own machinery. |
| `commons` plugin | `Money`, `Address`, `PhoneNumber`, `EmailAddress` | Used across multiple domain plugins; kernel never reads or writes. |
| Domain plugin | `HonduranID`, `RTN`, `BACAccountNumber` (commerce); `Conversation` (whatsapp) | Used by one plugin and its declared dependents. |

The test is **who writes the value, not who reads it.** If the kernel
manipulates a value in its own machinery, kernel. If multiple plugins
read/write but the kernel doesn't, commons. If only one plugin's
machinery touches it, that plugin.

## Strict rule for `commons`

`commons` is values-only. **No entities, no actions, no events, no
jobs, no views.** The moment one of those appears in `commons`, it has
stopped being commons and become a domain plugin in disguise. Move it
or split it.

## Plugin-specific docs

Each plugin owns its own design docs in `plugins/<name>/docs/`. The
kernel's design docs (in [`docs/core/`](../docs/core/)) are about the
SDK and kernel internals; plugin docs are about the plugin's domain.

## Status

Pre-code. The plugin packages are not yet implemented. Once the kernel
has its first vertical slice, `commons` is the second package to land
(values only — `Money`, `PhoneNumber`, `Address`, `EmailAddress`),
followed by `commerce`.
