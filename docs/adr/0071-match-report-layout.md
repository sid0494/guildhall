# 0071. The post-match report's layout

Status: Accepted (2026-11-10); layout superseded by [0074](0074-compact-report-table.md)

## Context

The report answers "did I do better than usual on this hero in this kind of
game?" at a glance. Values and averages are floats; some metrics need a parsed
match; a player's earlier games on a hero may be unparsed; a metric can be
removed while events naming it still wait.

## Options considered

- **Arrows:** compare raw values, which can show an arrow between two numbers
  that look the same, or compare them as displayed.
- **No average:** "first game", which is wrong for a parse-only metric when
  earlier games exist but were not parsed.

## Decision

The post mentions the player. The embed's author line is the hero's icon and
name; the title is "Win" or "Loss" (with a trophy or skull), the duration and
the kind; the edge is green for a win, red for a loss; the timestamp is when
the match was played. Each metric is an inline field, with its emoji and
label: "1,599 ▲ (avg 1,244)". Arrows follow each metric's direction (fewer
deaths is ▲) and compare the values as displayed, so equal-looking numbers
show "─". Without an average the value says "(first game)", or "(first parsed
game)" for parse-only metrics; a missing value says "—", or "— (needs a parsed
match)" on an unparsed match. A metric no longer registered is left out. The
registry carries each metric's emoji and decimal places; the event gains an
optional `duration_seconds`.

## Consequences

- Rendering is pure and tested case by case.
- Layout changes apply to events still waiting.
