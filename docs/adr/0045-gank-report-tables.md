# 0045. Gank Report keeps match rows and player rows, keyed by Dota account

Status: Accepted (2026-11-10)

## Context

Fetching per match and analysing per account
([ADR 0043](0043-fetch-per-match-analyse-per-account.md)) splits the spec's
`gank_report_matches` into a match level and a player level. Every reader of
player rows (`/match last`, `/trend`, the weekly report) has the
`LinkedAccount`, so any of its IDs could key them; they differ in what a
player's history means.

## Options considered

- **Discord user ID:** history belongs to the person; a smurf account linked
  later mixes its games into the same trends.
- **Link row ID:** not personal, but unlinking and linking again orphans the
  history, and re-pointing a link mixes accounts.
- **Dota account ID:** history belongs to the game account, which is what
  "is this account getting better?" asks; it is the player's own ID inside the
  match response, so recomputing from the archive needs no Discord context;
  it is a personal identifier stored in Postgres.

## Decision

- `gank_report_matches`: `match_id` (primary key), `played_at`,
  `duration_seconds`, `game_mode`, `lobby_type`, `parsed`, `fetched_at`. A row
  is written only when the fetch is final, so its existence is what an
  analysis waits for.
- `gank_report_player_matches`: `match_id` (foreign key) and `account_id`, the
  Dota account, together the primary key; `hero_id`, `player_slot`, `won`,
  `analyzed_at`.
- The spec's `s3_key` is dropped: the platform builds archive keys, and the
  plug-in reads by kind and ID. `hero_id` moves to the player row.
- Both writes are upserts (`INSERT ... ON CONFLICT DO UPDATE`), which pass the
  platform's statement check, so fetching or analysing again overwrites.
- B3's metrics are keyed by match, account and metric.

## Consequences

- Dota account IDs are in the database, which is private; they stay out of
  dedupe keys and logs, which use the link row ID.
- Two Discord users linking the same Dota account share one player row.
- Columns added later can be back-filled from the archive.
