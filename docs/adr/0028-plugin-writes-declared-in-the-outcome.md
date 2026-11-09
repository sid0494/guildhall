# 0028. Plug-in writes are declared in the outcome

Status: Accepted (2026-11-09)

## Context

ADR 0016 runs each handler outside any transaction, then claims the job's
dedupe key and writes the outbox in one short transaction. Gank Report is the
first plug-in that writes its own tables (matches and metrics), and those
writes must commit together with the claim, or a crash leaves data without a
claim or a claim without data. ADR 0016 left the mechanism to M2.

## Options considered

- **Run the handler inside the transaction:** full SQL power, but the
  transaction and its connection stay open during upstream HTTP calls (up to
  the 30 s job timeout), holding locks and idling in transaction; this is what
  ADR 0016 avoided.
- **Declared writes:** the handler returns SQLAlchemy insert, update and
  delete statements in its `Outcome`; the platform checks and runs them in the
  claim transaction. Short transactions, and table ownership is checked before
  anything runs. A handler cannot read its own writes or use a generated key.
- **A write function in the outcome:** the handler returns a coroutine the
  platform runs inside the transaction. Short and fully expressive, but the
  platform cannot see what it will do before it runs, and a function breaks
  the outcome's plain-value design (ADR 0009).
- **Direct writes that must be idempotent:** simplest, but not atomic with the
  claim; append-only data (Side Quest's price history) duplicates on a re-run.

## Decision

Declared writes:

- `Outcome.writes` is a tuple of SQLAlchemy DML statements, run in order in
  the claim transaction, after the claim and before the outbox rows.
- `check_outcome` rejects a write to a table the plug-in does not own, as a
  contract violation (dead-lettered with the reason, ADR 0018). A retry
  outcome carries no writes, as it carries no events or jobs.
- Reads during the handler go through a read-only connection; Postgres itself
  rejects writes on it.
- The contract now depends on SQLAlchemy: plug-ins define tables with
  SQLAlchemy Core and build statements from them. They still never import
  `guildhall.platform`.

## Consequences

- A job's own data, its claim, its events and its follow-up jobs commit
  together or not at all, and transactions stay short.
- "Each plug-in owns its tables" is enforced, not just a rule.
- Handlers cannot depend on database-generated keys or read back their own
  writes within a job; plug-ins use natural keys (`match_id`, `appid`) or keys
  generated in Python. If a plug-in needs more, the contract health check
  records why, and a write function can be added later as a defaulted field
  without breaking existing plug-ins.
- How plug-ins declare tables and migrations, and what `ctx.db` exposes, are
  decided next.
