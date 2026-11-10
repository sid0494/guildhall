# 0067. Dota links accept two ID forms, are checked by visible matches, and backfill

Status: Accepted (2026-11-10)

## Context

People know their Dota account as the account ID (`opendota.com/players/<id>`)
or as their 17-digit Steam ID, which is the account ID plus
76561197960265728. OpenDota answers `/players/{id}` with a profile even for
IDs that cannot exist, but returns no matches for them. The spec backfills
the 100 newest matches on linking. Old matches cannot be parsed, so waiting
for their parse costs five calls and fifteen minutes each for nothing; the
poll's first run reaches back a year and has the same problem.

## Options considered

- **The check:** the profile (present even for impossible IDs), or one call
  for the account's newest match.
- **The backfill:** the poll's logic with a wider net, a separate bulk
  implementation (no bulk endpoint exists for match details), or none (the
  first poll's 20 matches of the past year).
- **Old matches:** wait for a parse as now, or record them after one call.

## Decision

`dota_account_id` reads ASCII digits only (Python's `isdecimal` also accepts
other scripts' digits), converts a Steam ID, and requires 1 to 2**32 - 1.
`verify_account` rejects anything else without a call, then asks for one
match (`limit=1&significant=0`): none means "OpenDota can't see any matches
for that account", with the privacy setting to check. Accepted accounts are
stored as the account ID. `backfill`, the `on_link` job, runs the poll's code
with `limit=100` and no date window. `fetch_match` waits for a parse only
when the match ended at most seven days ago (`REPLAY_LIFETIME`, a
conservative guess at how long replays last); older ones are recorded after
one call.

## Consequences

- A link costs one call; a backfill at most about 101, about two minutes of
  the budget.
- A replay that outlives seven days is not parsed; an unparsed match keeps
  its basic metrics.
- Tests pin "now", since a match's age depends on when they run.
