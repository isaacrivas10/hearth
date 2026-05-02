# Spec: test harness

> **Source module**: `hearth/testing/harness.py`
> **Parent ADRs**: [ADR-0004 (SDK-first)](../../architecture/0004-sdk-first.md), [ADR-0007 (transactional contract)](../../architecture/0007-transactional-contract.md).

## Status

Stub. The harness is a load-bearing part of the slice — the slice's
test runs against this harness, and getting the shape right is one of
the slice's design exercises.

## Brief

The in-memory test harness plugin authors import from
`hearth.testing`. Provides:

- An in-memory `UnitOfWork` that implements the same Protocol as the
  Postgres-backed UoW.
- An in-memory event bus / outbox that fakes the transactional
  contract (events emitted before "commit" are visible after; events
  emitted in rolled-back actions are not).
- Plugin-set fixtures (`Harness(plugins=["commerce", "commons"])`)
  that load the requested plugins and refuse to load unrequested ones.
- pytest-asyncio integration with `asyncio_mode = "auto"` so plugin
  authors don't write `@pytest.mark.asyncio` everywhere.

## Sketch

```python
import pytest
from hearth.testing import Harness

@pytest.fixture
def harness():
    return Harness(plugins=["commerce", "commons"])

async def test_identify_customer_creates_new(harness):
    result = await harness.run(IdentifyCustomer(phone="+50499998888"))
    assert isinstance(result, Customer)
    assert harness.events_of_type(CustomerIdentified)
```

## Open questions

- **Fidelity to the real UoW.** The in-memory harness should be honest
  about ordering and visibility, but a SQLite-backed harness might be
  more accurate at the cost of speed. Possibly two harness modes.
- **Plugin-set fixture.** Lazy-loaded vs. eager.
- **Identity injection in test setup.** Which `Identity` runs the
  action.
- **Time control.** Freezegun-like fixtures for `fields.Timestamp`.
- **Event causation/correlation propagation in tests.**
