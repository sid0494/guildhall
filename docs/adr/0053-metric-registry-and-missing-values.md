# 0053. Metrics are registered functions; a NULL value means "no value"

Status: Accepted (2026-11-10)

## Context

Gank Report's metrics (last hits at 10 minutes, GPM, deaths per 10 minutes,
hero damage per minute, observer wards) are meant to change: the spec says to
try them for two weeks and prune. Two need a replay-parsed match, which some
matches never get ([ADR 0044](0044-parse-retry-policy.md)), and a game shorter
than 10 minutes has no last hits at 10. The spec's "GPM vs. my average on that
hero" mixes a value per match with a comparison.

## Options considered

- **Metric shape:** values and comparisons together, or values per match with
  comparisons made when reports are built.
- **A metric without a value:** no row, which cannot tell "the match gives
  none" from "the metric did not exist when the match was analysed"; or a row
  with a NULL value.

## Decision

`plugins/gank_report/metrics.py` holds `Metric(name, label,
higher_is_better, needs_parse, compute)`, where `compute(match, player)`
returns a float or None, and the ordered `METRICS` registry. A metric is a
value per match; the post-match arrow, the weekly trend and `/trend` compare
stored values, each with its own window. Every analysed player match gets one
row per registered metric, NULL where the match gives no value. A metric's
name is stored and must never be renamed.

## Consequences

- Adding a metric is a function and a registry line; a missing row then marks
  the matches still to compute.
- `AVG` and `COUNT(value)` skip NULLs, so missing values neither drag an
  average down nor count towards it.
- A test checks each metric's `needs_parse` against the recorded parsed and
  unparsed matches.
