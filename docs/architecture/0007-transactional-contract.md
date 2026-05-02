# ADR-0007: Transactional Contract — Outbox Pattern

## Status

Accepted.

## Context

Actions mutate entities and emit events. The question is what
guarantees the kernel makes about those two things relative to each
other and to the database transaction.

Three options exist, with very different correctness properties:

1. **Synchronous in-transaction dispatch.** Subscribers run
   immediately, inside the action's database transaction. If a
   subscriber raises, the transaction rolls back; entity changes are
   undone, and no event is recorded.
2. **Synchronous post-commit dispatch (no outbox).** The transaction
   commits; then subscribers run. If a subscriber fails between commit
   and dispatch (process crashes, etc.), the event is lost — the
   entity changes happened but no one is notified.
3. **Outbox pattern.** The action persists entity changes *and* writes
   the event to an outbox table inside the same transaction. The
   transaction commits both atomically. A separate dispatcher reads
   the outbox after commit and delivers events to subscribers, with
   retries until acknowledged.

Option (1) is what Django signals do by default and what naive
event-handling systems do. It is wrong for any system where
subscribers might fail and where upstream state shouldn't be rolled
back by downstream failure. Concretely: a `submit_payment` action
records a payment and emits `PaymentReceived`. If a `notify_customer`
subscriber to that event times out trying to reach WhatsApp, the
synchronous-in-transaction model rolls back the payment. The payment
really happened — the customer's bank moved money — but the database
forgets. This is a latent data-loss bug waiting for any flaky
subscriber.

Option (2) avoids the rollback bug but introduces a new one: events
can be silently lost between commit and dispatch. If the worker
process crashes after committing but before sending the WhatsApp
message, no retry; the event is gone.

Option (3) is the only design that gives plugin authors the contract
they actually want: *"if my action committed, my events are durable
and will be delivered eventually, and downstream failures are
downstream's problem."* It requires writing events to a table inside
the transaction and a separate dispatcher reading from that table —
small additional machinery, large correctness payoff.

The owner's stated position aligned with option (3) once the
distinction between "persisting events transactionally" and
"dispatching events synchronously" was made explicit. This ADR
captures that.

## Decision

The kernel implements the **transactional outbox pattern** as the
sole event delivery mechanism. There is no synchronous in-transaction
dispatch path. There is no "best-effort post-commit" dispatch path.

Specifically:

1. **Actions run inside a database transaction.** The kernel manages
   the transaction; plugins receive a `UnitOfWork` and use it to load
   and save entities. The transaction begins when the action starts
   and ends when the action returns or raises.
2. **Entity mutations and event records persist together.** Events
   emitted by an action are written to an `events` table (the outbox)
   in the same transaction as the entity mutations. ACID guarantees:
   if the action commits, both the entity changes and the event rows
   are durable; if the action fails, both roll back. There is no
   intermediate state where one exists without the other.
3. **Subscribers run after commit, asynchronously.** A dedicated
   dispatcher process (a kernel-shipped Job, see ADR-0005) reads the
   outbox in commit order and delivers events to registered
   subscribers. Subscriber execution is outside the action's
   transaction; subscriber failures cannot roll back the action.
4. **Delivery is at-least-once, not exactly-once.** The dispatcher
   retries failed deliveries with backoff. Subscribers must be
   idempotent. The kernel makes this contract explicit in the SDK and
   provides utilities (e.g., a `Once` decorator backed by a per-
   subscriber dedup table) for plugin authors who need exactly-once
   semantics at the subscriber.
5. **Subscribers are themselves transactional.** When a subscriber
   runs (potentially mutating entities or emitting more events), it
   does so inside its own UnitOfWork. The events it emits go into the
   outbox like any other action's events. Subscriber-emitted events
   thus form a causation chain that the dispatcher continues
   processing.
6. **Outbox order is preserved per aggregate.** Events from the same
   aggregate (entity instance) are dispatched in commit order.
   Events from different aggregates may be dispatched concurrently
   and may interleave. Plugin authors who care about cross-aggregate
   order must subscribe to multiple events and reconstruct the
   ordering they need (or model the dependency as a single
   aggregate).
7. **Synchronous in-transaction dispatch is not available.** Plugin
   authors will sometimes ask for it ("I just want to update this
   read model immediately when the entity changes"). The answer is
   always: subscribe to the event; the dispatcher will deliver it
   in milliseconds; if you need stronger consistency, use a view
   that reads the entity directly. There is no synchronous dispatch
   knob.

## Consequences

- **Plugin authors get a clean correctness contract.** "Action
  committed → events durable → subscribers will run" is the entire
  promise. No surprises about subscriber failures rolling back state.
- **The kernel needs a worker process.** The outbox dispatcher runs
  outside the request handler. This was already the lean for jobs
  generally (see CLAUDE.md open decision); the outbox confirms it.
- **Plugin authors must write idempotent subscribers.** This is
  documented loudly. The SDK helps where it can (the `Once` utility,
  natural idempotency keys, etc.), but the responsibility is real.
- **Latency from action to subscriber is non-zero.** Typically
  milliseconds; under load, more. Plugin authors who design as if
  subscribers are synchronous are designing wrong. The SDK's
  documentation, examples, and test harness all model the asynchronous
  reality.
- **The outbox is operationally visible.** The events table is a
  durable audit log of every state change in the system. Operators
  can inspect it, replay events into a new subscriber, or debug
  past behavior. This is a feature, not an artifact.
- **Subscribers can fan out arbitrarily.** Adding a new subscriber
  to an existing event is a pure addition with no risk to upstream.
  This is the pattern that makes multi-plugin extension actually
  composable in practice.
- **Long-running jobs triggered by events are natural.** Subscribers
  can enqueue jobs; jobs run independently with retry. The chain of
  action → event → subscriber → job is the kernel's offer for any
  asynchronous workflow.
- **Cross-plugin transactional boundaries are honest.** Plugin A's
  action commits its entities and its events; plugin B's subscriber
  is a separate transaction. Plugin authors can reason about
  boundaries because they correspond exactly to commits.

## Implementation sketch

(Non-binding; for orientation. Detailed design lives in the kernel
as it is built.)

- An `events` table in the database with columns: `id` (ULID for
  ordering), `aggregate_type`, `aggregate_id`, `event_type`,
  `payload` (JSON), `actor` (Identity), `causation_id`,
  `correlation_id`, `created_at`, `dispatched_at`, `attempts`.
- The kernel's `UnitOfWork` exposes `emit(event)` which appends to
  an in-transaction buffer; on transaction commit, the buffer is
  flushed to the `events` table as part of the same SQL transaction.
- The dispatcher is a worker that polls (or uses LISTEN/NOTIFY on
  Postgres) for new outbox rows, claims them with row-level locks,
  delivers them to all registered subscribers, and marks them
  dispatched. Failed deliveries get retried with exponential
  backoff; permanently failing events get parked in a dead-letter
  state for operator review.
- Subscribers run inside their own `UnitOfWork`, which gives them
  the same transactional guarantees as actions.
- A "replay" command exists for operators: re-dispatch events
  matching a filter, with optional rewind to a specific subscriber.

## Alternatives considered

- **Synchronous in-transaction dispatch.** Rejected for the rollback
  bug above. This is the most common naive design and the most
  common source of latent data-loss bugs in event-driven systems.
- **Synchronous post-commit dispatch with no outbox.** Rejected
  because events can be silently lost between commit and
  dispatch.
- **Two-phase commit between the database and an external message
  bus (Kafka, RabbitMQ).** Rejected as massive complexity for no
  gain over the outbox in our context. Two-phase commit also
  doesn't actually exist in the form most teams want; the outbox
  is the pragmatic substitute.
- **Event sourcing as the primary storage model.** Considered.
  Rejected because event sourcing is a much larger commitment with
  much steeper learning curve for plugin authors and large
  query-side complexity. The outbox pattern gives us an audit log
  and reliable event delivery without forcing event sourcing on
  plugins. Plugins that want event-sourced subaggregates can build
  them on top.
- **Letting plugins choose synchronous vs. asynchronous dispatch
  per subscriber.** Rejected per the principle in ADR-0001:
  configurable middle-ground designs are wrong. The kernel commits
  to one event-delivery contract; plugins live with it.
- **Outbox dispatched in-process (no worker).** Possible for tiny
  deployments but fragile under load and confusing as a default.
  The kernel will probably support an "in-process dispatcher" mode
  for the test harness and for development, but production
  deployments use the worker.

## References

- ADR-0001 — vision (single contract, no configurable middle).
- ADR-0003 — plugin model (in-process; subscribers are
  registered).
- ADR-0005 — primitives (Action, Event, Job).
- ADR-0006 — ORM contract (UnitOfWork, transactions).
