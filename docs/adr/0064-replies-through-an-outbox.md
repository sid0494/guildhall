# 0064. Command replies go through an outbox

Status: Accepted (2026-11-10)

## Context

A command's reply edits the deferred message through the interaction's
webhook (`PATCH /webhooks/{application}/{token}/messages/@original`), within
15 minutes. Sending before the command's writes commit can announce something
that then fails to commit; committing first and then sending can lose the
reply for good, because a retried job finds its key already taken. Editing
the original message is idempotent.

## Options considered

- **An outbox of replies, committed with the command's writes, sent by a
  worker loop:** the reply is sent if and only if the command took effect.
- **Commit, then send directly:** simpler, but a send that keeps failing loses
  the reply.
- **Send, then commit:** can report an effect that never happened.

## Decision

`outbox_replies` holds each reply (application ID, interaction token, message,
attempts, sent or abandoned time), inserted in the command's transaction. A
reply dispatcher, one of the worker's dispatcher loops, takes pending replies
with `FOR UPDATE SKIP LOCKED` and edits them in, with no Authorization header
(the token in the path is the credential) and `allowed_mentions` empty, so a
reply never pings anyone. A `429` stops the pass for Discord's
`retry_after` (at most a minute); a `5xx` or network error retries after 2, 4,
8 and 16 seconds and is abandoned after five attempts; any other `4xx` is
abandoned; a reply still pending after 14 minutes is abandoned unsent. Sent
and abandoned replies are pruned after a day. A command dead-lettered by the
runner, or failing on its last allowed receive, gets "Something went wrong
with that command." queued instead.

## Consequences

- At-least-once delivery is safe because edits are idempotent.
- Tokens are deleted a day after use.
- C3's channel posts use the bot token and their own dispatch over
  `outbox_events`, sharing the Discord settings.
