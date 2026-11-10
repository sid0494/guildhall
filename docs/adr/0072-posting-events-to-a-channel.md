# 0072. Events are posted to one channel, with a nonce against duplicates

Status: Accepted (2026-11-10)

## Context

Match reports are posts, not replies: the worker creates them with the bot
token. Creating a message is not idempotent: a dispatcher that posts and then
fails to record it posts again. The spec had left "accept rare duplicates"
pending. Discord lets a message carry a `nonce` and `enforce_nonce`: within a
few minutes, the same nonce from the same bot returns the message already
made. Events can wait while the worker is down.

## Options considered

- **Where:** one configured channel, with a mention of the player; a direct
  message per player (fails per user with DMs closed, and hides friends'
  reports); a channel per plug-in (configuration before a second plug-in
  exists).
- **Duplicates:** accept them; a nonce; or a two-phase record checked against
  channel history after a crash (exactly once, but heavy).

## Decision

Posts go to `GUILDHALL_DISCORD_CHANNEL_ID` and mention the player, notifying
only them. `outbox_events` gains `attempts`, `sent_at`, `abandoned_at` and
`discord_message_id`. An event dispatcher, beside the reply dispatcher, takes
pending events with `SKIP LOCKED`, rebuilds and renders each (ADR 0069), and
posts it with `nonce: "evt-<id>"` and `enforce_nonce: true`, storing Discord's
message ID. Discord's answers follow the reply rules (ADR 0064), now shared in
`platform/discord.py`. An event still undelivered a day after it was recorded
is abandoned rather than posted late. Settled events are pruned after a week.
Without a channel and a bot token the worker posts nothing and says so.

## Consequences

- A duplicate needs a crash and a retry more than a few minutes apart: rare,
  and accepted.
- Message IDs allow editing a post later (for example once a slow parse
  arrives).
- A per-plug-in channel can be added as an override later.
