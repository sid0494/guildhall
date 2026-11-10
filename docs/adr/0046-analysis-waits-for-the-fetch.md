# 0046. An analysis waits for its fetch, then fails loudly

Status: Accepted (2026-11-10)

## Context

`analyze_match` is queued with `fetch_match` and usually runs before the fetch
finishes, which takes up to about 15 minutes normally
([ADR 0044](0044-parse-retry-policy.md)) and longer in a backfill burst, when
throttled fetches are deferred ([ADR 0033](0033-throttled-jobs-are-deferred.md)).
A fetch that keeps failing is dead-lettered after 5 receives.

## Options considered

- **When to check:** immediately (almost always wasted) or after a delay.
- **How often:** growing intervals (fewer checks, but the report can lag the
  fetch by 10 to 15 minutes) or a fixed 5 minutes (one read of the plug-in's
  own table each, no upstream call).
- **How long:** forever (hides a dead-lettered fetch), 15 to 30 minutes (too
  short for a backfill), or about two hours.
- **Missing data after the wait:** finish quietly, retry, or fail permanently.

## Decision

`poll_matches` queues the analysis with a 5-minute delay. While the match row
is missing, it retries every 5 minutes (`ANALYZE_RETRY_DELAY`), up to 24
attempts (`ANALYZE_MAX_ATTEMPTS`, about two hours), then fails permanently:
"match was never fetched". A match row without its archive, a linked account
missing from the match, and a linked account that is not a Dota account ID
also fail permanently. None of these can fix themselves, and a committed
fetch's archive always exists
([ADR 0031](0031-raw-archive-declared-in-the-outcome.md)).

## Consequences

- A normal match logs two or three `not_ready` lines for its analysis.
- Each failure leaves a dead-lettered job with a stated reason; after the
  cause is fixed (for example, redriving the failed fetch), redriving the
  analysis completes it.
- Failed runs also count towards the 24 attempts, which leaves ample margin.
