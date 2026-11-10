# 0052. A poll queues each match once per account, recorded with its jobs

Status: Accepted (2026-11-10)

## Context

Every 15 minutes `poll_matches` lists an account's recent matches, most of
which it has already handled. Job dedupe happens when a job runs, not when it
is queued: re-queuing a finished match costs a no-op run, but re-queuing one
still waiting for its parse runs a second fetch in parallel, and a
dead-lettered job never claims its key, so re-queuing it fails it again every
15 minutes. A poll's writes and jobs commit atomically with its claim
([ADR 0028](0028-plugin-writes-declared-in-the-outcome.md)). The first poll
after linking sees nothing as handled.

## Options considered

- **Re-queue every listed match and rely on dedupe:** stateless, but 40
  messages and log lines per account per poll, parallel fetches, and a
  dead-letter queue that refills itself.
- **A high-water mark per account:** one row each, but it assumes matches are
  listed in order; one listed late is skipped for good.
- **A unique key on the job outbox:** generic, but sent rows are pruned after
  seven days, so the guarantee quietly expires.
- **A table of matches already queued per account:** exact, no ordering
  assumption, a few bytes per match.

## Decision

`gank_report_polled_matches(account_id, match_id, polled_at)`. A poll asks
OpenDota for the 20 newest matches of the past year (`limit=20`, `date=365`,
`significant=0`), keeps the tracked ones
([ADR 0051](0051-tracked-modes-normal-and-turbo.md)), reads which of them are
already polled for the account, and for each new one returns `fetch_match`
and `analyze_match` (delayed 5 minutes) together with an insert of the polled
rows (`ON CONFLICT DO NOTHING`), in one outcome.

## Consequences

- A match is queued once per account; a failed job stays in the dead-letter
  queue until it is redriven.
- Two overlapping polls may both queue a match; run-time dedupe makes the
  second a no-op, and their marks do not conflict.
- The first poll after linking queues up to 20 matches of the past year;
  older ones probably cannot be parsed and are recorded unparsed. Anything
  older or beyond those 20 is left to the backfill that comes with `/link`.
- If no worker runs for over a year, matches older than that are not polled.
- Revisit the outbox option if Side Quest needs the same guarantee.
