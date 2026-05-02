# ADR-0002: Single-tenant Deployment Model

## Status

Accepted.

## Context

Hearth must answer: when an operator deploys a Hearth instance, what is
running, where, and against what data? This is the question that
distinguishes a SaaS product from a self-hosted platform from a library,
and it has cascading implications for the plugin model, the ORM, the
identity model, and the security boundary.

Three deployment models were on the table:

1. **Multi-tenant SaaS** — Hearth runs the infrastructure; operators are
   tenants in a shared kernel. One control plane, one shared database
   (with row-level isolation), one ops team.
2. **Multi-tenant self-hosted** — operators deploy Hearth and serve
   multiple of _their own_ customers from one instance. Row-level
   isolation, tenant-aware queries throughout the kernel, noisy-neighbor
   handling.
3. **Single-tenant self-hosted** — operators deploy one Hearth instance
   per business they run. One database per instance. No tenant concept in
   the kernel at all.

Multi-tenancy is the source of a disproportionate amount of complexity in
business software: tenant context threading through every query, row-level
security policies, per-tenant migrations, noisy-neighbor isolation,
billing-per-tenant, support-per-tenant. The benefits accrue to the
operator who runs many tenants. Hearth's first operator runs one business
on one instance, and the project's vision (ADR-0001) is technical
operators running their own infrastructure — not a SaaS vendor.

The operator's mental reference for the right shape is Snowflake's
relationship to cloud providers: Snowflake runs on AWS, Azure, or GCP, but
Snowflake doesn't try to abstract over them or be a meta-cloud. The cloud
provider is the operator's choice; Snowflake is portable across them. We
want the same posture toward the database and the host: Hearth is
portable, the infrastructure is the operator's.

## Decision

Hearth deployments are **single-tenant**. One kernel + chosen plugins →
one Docker container → one database. There is no tenant concept inside
the kernel.

Specifically:

1. **One container per business.** An operator running multiple businesses
   runs multiple Hearth instances, possibly with different plugin sets per
   instance. They are independent at every layer: separate database,
   separate background workers, separate domain, separate logs.
2. **The operator chooses the database.** The kernel speaks SQLAlchemy
   (see ADR-0006), so any SQLAlchemy-supported relational database works
   in principle. PostgreSQL is the supported and tested target;
   SQLite is supported for development and the test harness; other
   databases are best-effort. The operator points the kernel at a
   connection string; the kernel does not provision, manage, or back up
   the database.
3. **The plugin set is decided at build time.** A `plugins.toml` (or
   equivalent) manifest declares which plugins this instance includes.
   The build produces a container image with exactly those plugins. The
   image _is_ the instance's identity. Two instances with different
   plugin sets are different software, not different configurations.
4. **The kernel ships no infrastructure.** No reverse proxy, no
   certificate management, no log shipping, no metrics backend. The
   operator runs Hearth behind whatever infrastructure they already use.

## Consequences

- The kernel codebase is dramatically simpler. No tenant ID threading
  through every entity, action, query, or event. No row-level security.
  No per-tenant migration coordination. The mental model is "this is the
  database; everything in it belongs to this instance."
- Security boundaries are clearer. Cross-business data leaks are
  impossible by construction because cross-business data does not exist
  in any database.
- Horizontal scale is the operator's problem, not the kernel's. A Hearth
  instance scales the way any single-database stateful service scales:
  read replicas, vertical scaling, careful connection pooling, an outbox
  worker that can be horizontally scaled. The kernel does not pretend
  otherwise.
- Operators who need to run many tenants must build a control plane that
  provisions Hearth instances per tenant. This is fine; it's a different
  product, and it can use Hearth without modification.
- "Spin it up immediately" — one of the operator's stated goals — is
  trivially achievable. `docker run hearth/<plugin-set>:<version>` against
  a Postgres URL is the entire deployment.
- The kernel cannot become an Airbnb-for-stores or a marketplace platform
  without a deliberate, separate effort to build a multi-tenant layer
  _outside_ the kernel. We accept this.

## Alternatives considered

- **Multi-tenant kernel, optional single-tenant deployment.** Considered
  and rejected. "Optional multi-tenancy" is the worst of both worlds: the
  kernel still pays the complexity cost (tenant column on every table,
  every query parameterized by tenant), and operators who don't need it
  pay too. Multi-tenancy is one of those features that has to be all-in
  or all-out; in-between is a maintenance trap.
- **Multi-tenant SaaS with Hearth-hosted instances.** Out of scope per
  ADR-0001 (Hearth is software, not a service). Also: the project owner
  doesn't want to operate infrastructure for other people.
- **Database-agnostic via a port/adapter pattern over multiple drivers
  (Postgres, MySQL, Mongo, etc.).** Rejected. SQLAlchemy already gives us
  cross-relational-database portability for free, and the entity model
  is fundamentally relational (entities, references, transactions).
  Supporting non-relational stores would force a lowest-common-denominator
  data model that hurts every plugin author. PostgreSQL is the target;
  SQLite is the dev/test target; everything else is best-effort.
- **Kubernetes-native deployment with operators and CRDs.** Out of scope.
  Operators who want this can build it on top of the container; the
  kernel does not assume Kubernetes.

## References

- ADR-0001 — vision and non-goals.
- ADR-0003 — plugin model (build-time manifest, in-process plugins).
- ADR-0006 — ORM contract (SQLAlchemy underneath).
