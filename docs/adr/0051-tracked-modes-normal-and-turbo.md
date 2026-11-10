# 0051. Matches are tracked as normal or Turbo; the rest are skipped

Status: Accepted (2026-11-10)

## Context

Some players Gank Report serves play only Turbo, some a mix of Turbo, ranked
and unranked. Turbo's economy and pace differ, so its GPM or last hits would
distort a baseline built from normal games. OpenDota's default match-list
filter (`significant=1`) drops Turbo together with unbalanced modes and very
short games, so including Turbo means turning that filter off and applying
our own. OpenDota's constants mark which game modes and lobby types are
balanced.

## Options considered

- **OpenDota's default filter:** no Turbo at all.
- **Track every match, filter in reports:** spends calls and storage on games
  no report shows.
- **A rule of our own, applied before fetching:** decided from fields the
  match list already has, so skipped matches cost nothing.

## Decision

`tracked_mode(game_mode, lobby_type, duration_seconds)` in
`plugins/gank_report/modes.py`:

- `normal`: a balanced draft mode (All Pick, Captains Mode, Random Draft,
  Single Draft, All Random, Least Played, Captains Draft, Balanced Draft, All
  Draft) in an unranked, ranked or Battle Cup lobby. Ranked and unranked are
  compared together.
- `turbo`: Turbo in an unranked lobby.
- Anything else is not tracked: other modes, private (practice) and
  tournament lobbies, bot games, and any game shorter than five minutes
  (remakes, early abandons).

Polls ask for `significant=0` and skip untracked matches; `fetch_match` refuses
one permanently, before asking for a parse. The kind is stored in
`gank_report_matches.mode`, with a check constraint allowing only `normal`
and `turbo`. Post-match reports compare against the same kind; weekly reports
have one section per kind played that week.

## Consequences

- In-house games in private lobbies are not tracked.
- Changing the rule later means re-deriving `mode` from the stored
  `game_mode`, `lobby_type` and duration, a one-off update.
- Alembic does not detect check constraints, so the constraint was added to
  its migration by hand and has its own integration test.
