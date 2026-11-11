# 0074. Match reports show one row per stat, marked green or red

Status: Accepted (2026-11-10)

## Context

The first layout ([ADR 0071](0071-match-report-layout.md)) gave each stat an
inline field. Discord's phone apps stack fields one per row, which read well;
its desktop app lays inline fields three across, which looked cluttered. The
arrows meant "better" (▲) or "worse" (▼), which reads oddly for deaths, where
fewer is better. Labels such as `LH@10` read poorly.

## Options considered

- **A compact monospace table** (tried): one aligned line per stat. Tidy on
  desktop, but alignment depends on how wide each device draws emoji, and the
  owner preferred the stacked fields on a phone.
- **One line of text per stat:** half the height, but less separation.
- **Stacked fields:** the first layout's fields, full width everywhere.
- **Markers:** coloured circles only (colour-blind unfriendly, no direction),
  tick or cross (no direction), Discord's `ansi` coloured text (not coloured
  on the phone apps), or a colour for better or worse plus an arrow for the
  actual direction.

## Decision

Each stat is a full-width field (`inline=False`): its emoji and label as a
phrase ("Last Hits @ 10 min", "Gold per min", "Deaths per 10 min", "Hero
Damage per min", "Observer Wards") above its value. The value carries a
marker: 🟢 for better or 🔴 for worse, with ▲ or ▼ for the direction it
actually moved, so fewer deaths is 🟢▼; equal-looking values show ⚪─, and a
stat without an average shows ⚪· and "first". A missing value on a parsed
match shows "—". Stats an unparsed match lacks are left out and named in the
footer, which also says what is compared ("vs. your Turbo games on Sniper").
`Embed` gains `footer`. The author line, title, colour and timestamp of ADR
0071 stay.

## Consequences

- The card is taller than a table: two lines per stat.
- Nothing needs aligning, so it reads the same on every device.
- ADR 0071's layout is superseded; its other rules stand.
