# 0070. Gank Report ships a hero table of names and icon paths

Status: Accepted (2026-11-10)

## Context

Events carry a hero's ID; posts show the hero's name and icon, and `render`
cannot do I/O. OpenDota's `/api/constants/heroes` lists every hero (127, about
90 KB of stats), including the paths of its portrait and minimap icon on
Valve's CDN. Valve adds or renames a hero once or twice a year; internal names
can lag (Wraith King's files are still `skeleton_king`).

## Options considered

- **A table shipped with the plug-in, refreshed by a script:** a lookup at
  render time; a manual refresh when heroes change.
- **The name in each event, fetched during analysis:** always current, but a
  call per analysis for data that changes twice a year.
- **A Postgres table refreshed by a scheduled job:** current without per-call
  cost, but render still cannot read it, so the name would also travel in the
  event.
- **The image:** the 256x144 portrait as a thumbnail (it narrows the fields
  beside it on phones), or the 32x32 icon in the embed's author line, shown at
  about 24 px.

## Decision

`scripts/update_heroes.py` writes `plugins/gank_report/heroes.json`: each
hero's name and icon path, sorted by ID, about 13 KB. The plug-in loads it at
import; a hero newer than the table renders as "Hero <id>" without an icon.
Posts show the icon in the author line, from Valve's CDN; no image is
downloaded or committed.

## Consequences

- The table is refreshed by hand when Valve adds or renames a hero.
- If Valve moves its images, icons disappear until the next refresh; posts
  still work.
- `heroes.json` is package data: the image build must include it (checked).
