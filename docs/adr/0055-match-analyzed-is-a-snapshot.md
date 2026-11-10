# 0055. `gank_report.match_analyzed` carries a snapshot of the report

Status: Accepted (2026-11-10)

## Context

The post-match report shows the hero, the result and each metric with an
arrow against the player's average on that hero in the same kind. `render()`,
which turns an event into a Discord message, does no I/O, so it cannot read
those averages; `analyze_match` can.

## Options considered

- **A thin event (match and link IDs) that render looks up:** breaks the no-I/O
  rule, or needs a contract change giving render database access, with render
  code that can fail or be slow.
- **A snapshot of everything the post shows, computed when analysing:** render
  is pure formatting; one extra read per posted analysis.
- **Values without comparisons:** no read, but no arrows, which is the point of
  the report.

## Decision

`MatchAnalyzed` (type `gank_report.match_analyzed`, schema version 1) holds
`match_id`, `discord_user_id`, `hero_id`, `won`, `mode`, `parsed`,
`played_at` and, per registered metric, `MetricResult(name, value, baseline,
baseline_games)`. The baseline is the average over the player's at most 20
games on the same hero and kind played before this match, counting only games
with a value; `baseline_games` is how many. One query computes every metric's
baseline. The key is `gank_report:match_analyzed:<match id>:<link id>`.

## Consequences

- A delivered post shows the comparison as of the analysis, even if delivered
  late.
- Labels and directions come from the registry at render time; hero names
  from constants shipped with the plug-in (C3).
- Chosen focus metrics, once `/focus` exists, are an additive field.
