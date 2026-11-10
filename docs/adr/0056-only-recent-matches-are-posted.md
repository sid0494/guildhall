# 0056. Only matches that ended in the last 12 hours are posted

Status: Accepted (2026-11-10); to be revisited once posts are in use.

## Context

Analyses run for a match just played, for up to 20 matches of the past year
on the first poll after linking, for a backfill of up to 100, and for
recomputes. Re-posting is already impossible: the event outbox's dedupe key
is unique and events are never pruned, so an event with a used key is
dropped. The question is which analyses should post at all.

## Options considered

- **Post every analysis:** linking an account floods the channel with up to
  20 posts about old games.
- **Post only matches that ended recently, decided in `analyze_match`:** one
  rule covers polls, backfills and recomputes, with nothing to pass along.
- **An `announce` flag in the payload, set by whoever queues the analysis:**
  explicit, but the poll still needs an age rule for its first run, and every
  caller must set it.

## Decision

`analyze_match` emits the event only if the match ended (`played_at +
duration`) at most 12 hours before the analysis (`POST_WINDOW`); otherwise it
records the metrics and skips the baseline read. The plug-in takes a clock,
the real UTC time by default, so tests can fix the time.

## Consequences

- 12 hours covers the poll, the parse wait and an analysis held up in a
  backfill, and an evening's outage.
- A game played during a longer outage is recorded without a post; it still
  shows in `/match last` and the trends.
- The window is listed as an open question in the spec, to revisit once
  posts are in use.
