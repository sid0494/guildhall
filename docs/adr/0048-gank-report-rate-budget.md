# 0048. Gank Report budgets 50 OpenDota calls a minute and 2,000 a day

Status: Accepted (2026-11-10)

## Context

OpenDota's free tier, without a key, allows 60 calls a minute and 3,000 a day
per IP address, read from its `x-rate-limit-remaining-*` response headers on
2026-11-10; the spec had said 2,000 a day. Everything from one address shares
those limits, including calls made by hand. Steady use is about 30 calls a
day; a 100-match backfill is at most about 500.

## Options considered

- **The upstream limits exactly:** uses every call, but a moving window that
  does not line up with OpenDota's, and any other caller on the address, cause
  occasional `429`s, each pausing every Gank Report job.
- **Limits with headroom:** fewer calls, no expected `429`s.
- **Adapting live to the remaining-calls headers:** precise, but needs platform
  support for something only one upstream offers.

## Decision

`RateLimitPolicy(per_minute=50, per_day=2_000)`.

## Consequences

- A 100-match backfill takes about ten minutes of budget.
- A thousand calls a day remain for development and recording fixtures.
- The spec's figure is corrected to 3,000 a day per IP.
