# 0074. Match reports show a compact table, with context in the footer

Status: Accepted (2026-11-10)

## Context

The first layout ([ADR 0071](0071-match-report-layout.md)) gave each stat an
inline field: an emoji, a label, the value, an arrow and "(avg ...)", under a
sentence of description. Seen in Discord, the five fields with their repeated
text looked cluttered.

## Options considered

- **A compact table:** one monospace block, a line per stat, value, arrow and
  average in aligned columns; context in the footer.
- **Slim fields:** Discord's grid kept, labels shortened, the average under
  each value.
- **Headline lines:** one sentence per stat with a percentage, averages only
  for notable changes.

## Decision

The embed's description is a code block: a header ("you", "avg") and one line
per stat, starting with its emoji and its label as a phrase ("Last Hits @ 10
min", "Gold per min", "Deaths per 10 min", "Hero Damage per min", "Observer
Wards"; emoji and phrases at the owner's request, abbreviations read poorly),
the value, the arrow and the average, right-aligned in columns sized to the
values shown. A stat without an average shows "·" and "first"; a missing value
on a parsed match shows "—". Stats an unparsed match lacks are left out and
named once in the footer, which also says what is compared ("vs. your Turbo
games on Sniper"). `Embed` gains `footer`. The author line, title, colour,
timestamp and arrow rules of ADR 0071 stay.

## Consequences

- Lines run to about 42 characters, which can wrap in a narrow phone view.
- Discord draws an emoji about two monospace columns wide; every stat line
  starts with one and the header is indented to match. Checked by eye in
  Discord.
- ADR 0071's layout is superseded; its other rules stand.
