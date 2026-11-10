# 0054. Metric values are stored in long format

Status: Accepted (2026-11-10)

## Context

The spec left open whether metric values are stored one row per metric or
one column per metric, leaning towards rows. Metrics are added and pruned
over time ([ADR 0053](0053-metric-registry-and-missing-values.md)), and every
report averages them the same way.

## Options considered

- **Long format, a row per match, account and metric:** no migration per
  metric; one query shape for every metric; five rows per match.
- **Wide format, a column per metric:** the simplest SQL for a fixed set, but
  every metric added or removed is a migration and a backfill, and report code
  names every column.
- **One JSONB column of all metrics:** no migration, one row per match, but
  averages need extraction and casts, values are untyped, and a missing key
  and a null value look alike.

## Decision

`gank_report_metrics(match_id, account_id, metric, value)`, the first three
the primary key, `value` a nullable `DOUBLE PRECISION`, with a foreign key to
`gank_report_player_matches`. A match's metrics are written as one multi-row
upsert.

## Consequences

- A few thousand rows a year per active player.
- Removing a metric is deleting its rows.
- Values are floats; rounding is for display only.
