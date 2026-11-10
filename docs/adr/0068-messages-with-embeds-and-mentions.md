# 0068. Messages carry embeds and the users they may notify

Status: Accepted (2026-11-10)

## Context

`Message` began as text, for command replies. Match reports need a layout that
reads well on a phone right after a game, a colour for the result, the
player's hero, and a mention that notifies the player and nobody else. Replies
and posts should become Discord's JSON in one place.

## Options considered

- **Plain text** with a code-block table: no contract change, but alignment
  breaks on phones.
- **Embeds**, Discord's cards: fields laid out by Discord, a coloured edge, a
  timestamp in each viewer's time zone, an author line with an icon.
- **Mentions:** let Discord parse every mention in the text, or list the users
  a message may notify.

## Decision

`Message(content="", embeds=(), mentions=())` needs content or an embed.
`Embed(author, title, description, colour, fields, timestamp)`,
`EmbedAuthor(name, icon_url)` (HTTPS only) and `EmbedField(name, value,
inline)` carry Discord's own limits, so an oversized message fails where it
is built. `discord_message()` is the single conversion to Discord's JSON;
`allowed_mentions` is always `{"parse": [], "users": <mentions>}`, so only the
listed users can be notified, never @everyone or a role. `Message` moved to
`contract/messages.py`; replies are stored without default fields.

## Consequences

- The bot needs Embed Links in the channels it posts to.
- New embed parts (footers, images) are additive.
