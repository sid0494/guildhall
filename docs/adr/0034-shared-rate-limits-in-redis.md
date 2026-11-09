# 0034. Shared rate limits: moving windows in Redis with the `limits` library

Status: Accepted (2026-11-09)

## Context

Each plug-in declares a `RateLimitPolicy` (`per_minute`, `per_day`) for its
upstream API. OpenDota's free tier allows about 60 requests a minute and
2,000 a day; backfilling a newly linked account makes 100 calls at once, from
up to 10 concurrent jobs per worker and several worker replicas. The budget
must be shared by every replica, and checking it and taking from it must be
one atomic step, or two replicas can both take the last call.

## Options considered

- **A limiter in each process** (for example `aiolimiter`): simple, but each
  replica sees only its own calls, so N replicas allow N times the budget.
- **Postgres** (a row per plug-in, locked per call): shared and atomic, but
  every upstream call adds a locking transaction to the database.
- **Our own token bucket as a Redis Lua script**: atomic (Redis runs a script
  with nothing in between) and can check both limits in one step, but it is
  our code and our edge cases. A token bucket also allows a burst of the
  whole minute's budget at once, which some upstreams punish.
- **The `limits` library on Redis**: maintained, with tested Lua scripts for
  moving, sliding and fixed windows. A moving window ("at most N calls in any
  60 s") matches how upstreams usually count. No single call checks two
  limits, and there is no "wait until allowed", only how long until a slot
  frees.

## Decision

`limits` (5.8) with moving windows, through the official `redis` client:

- Each plug-in has a minute window and a day window keyed by its name.
  `acquire` takes from the minute window first, then the day window. When the
  day refuses, the minute slot is already spent: the error is towards fewer
  calls, never more.
- A refused call is told how long until the window's oldest call leaves it.
- A `429` sets `guildhall:cooldown:<plug-in>` with an expiry; while it exists,
  every replica's `acquire` answers with its remaining time.
- The limiter and the cooldown share one connection pool, created lazily, so
  the worker starts while Redis is down. Any Redis error raises
  `LimiterUnavailableError`, and the call fails closed (ADR 0033). This
  settles the spec's pending decision on failing closed.

## Consequences

- Upstream budgets hold across any number of replicas.
- A moving window stores one timestamp per call in the window: 60 + 2,000
  entries for OpenDota, negligible. Very large budgets (the synthetic
  plug-in's) cost memory only when calls are actually made.
- Windows use each worker's clock; with NTP the skew is milliseconds.
- Redis holds only rebuildable state: after a restart, budgets start empty,
  which can allow one extra window's worth of calls at most.
- One budget per plug-in, as the spec says. Side Quest will call two APIs
  with different limits; that will need per-upstream budgets, a contract
  change recorded when it arrives (contract health check).
