# 0073. `/match last` rebuilds the report from stored rows

Status: Accepted (2026-11-10)

## Context

`/match last` shows the player's latest analysed match as the same card the
channel post uses. The post renders a `MatchAnalyzed` event, a snapshot made
at analysis time; a command has no event. Events are pruned a week after
posting, and matches recorded by a backfill were never posted. Plug-ins cannot
read Discord.

## Options considered

- **Rebuild the snapshot from stored rows:** the latest player match, its
  metric rows and the baselines as of that match, then the same renderer.
- **Fetch the posted message by its stored ID:** needs plug-ins to read
  Discord, and fails for pruned events and unposted matches.
- **Store each rendered report:** freezes the layout and duplicates data.

## Decision

The command (public, no options) reads the caller's latest analysed match by
`played_at`, its metric rows (in the registry's order), and the baselines with
the same query `analyze_match` uses, as of that match; it builds a
`MatchAnalyzed` and replies with `render_match(event, mention=False)`, since
the person asking needs no notification. Without a link it says how to link;
with nothing analysed yet it says reports follow a game. The latest match may
be of either kind.

## Consequences

- The reply matches the post, comparisons included, even after later games.
- Three reads of the plug-in's own tables; no upstream calls.
- A `kind` option can be added later.
