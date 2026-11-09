# 0037. Missed ticks fire once; new schedules wait for their next tick

Status: Accepted (2026-11-09)

## Context

Everyday use runs on a laptop that sleeps, so no worker may be running when a
tick falls due: Sunday's 18:00 report can pass while the lid is closed. When a
worker returns, it must decide what to do with the ticks it missed. A schedule
seen for the first time (a new deploy, a new schedule) raises the same
question about ticks before it existed.

## Options considered

- **Skip missed ticks:** fire only on schedule from now on. A missed Sunday
  means no weekly report that week.
- **Fire every missed tick:** after a night asleep, 96 `poll_matches` jobs at
  once, each doing the same work.
- **Fire once to catch up:** one run for the most recent missed tick.

## Decision

Fire once to catch up. Each pass fires only the latest tick at or before now,
so any number of missed ticks coalesce into one run, whose `scheduled_for` is
that latest tick.

A schedule seen for the first time records its latest tick as already fired,
so it first fires at its next tick. Deploying on a Monday does not post the
previous Sunday's report.

## Consequences

- After the laptop wakes on Monday, the weekly report posts once, knowing from
  `scheduled_for` which week it covers; polling resumes with one poll.
- A tick missed by more than one period is never fired as itself. Handlers
  that need to cover the gap (a poll after a long sleep) work from their own
  state, not from the tick count; Gank Report's poll asks for recent matches
  and skips the ones it has.
- A brand-new schedule can wait up to one period before its first run.
