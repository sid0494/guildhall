# 0061. How the interactions endpoint handles a request

Status: Accepted (2026-11-10)

## Context

Discord expects an answer within three seconds and may send an interaction
again after a network error. The spec requires recording the interaction ID
under a unique constraint before any side effect. Commands arrive with C2;
until then the endpoint still has to answer.

## Options considered

- **When to record the ID:** first in its own transaction, or in the same
  transaction as the interaction's side effects (outbox rows).
- **A retry:** answer again, or refuse.
- **Before commands exist:** an error, or a private placeholder reply.

## Decision

1. Verify the signature over the raw body; `401` if it fails
   ([ADR 0059](0059-interaction-signatures.md)).
2. Parse the body; `400` if it is not an interaction.
3. Answer `PING` with `PONG`, recording nothing.
4. Record the ID in `interactions_seen` with `INSERT ... ON CONFLICT DO
   NOTHING RETURNING`; a returned row means first time. From C2, the side
   effects (outbox rows) go in the same transaction. If the ID cannot be
   recorded, answer `503`.
5. A retry gets the same answer and does nothing again.
6. Until commands exist, every command gets a private (ephemeral) "not yet"
   reply.

`/healthz` reports liveness and `/readyz` that Postgres answers. The app
depends on an `InteractionLog` protocol, implemented in Postgres. The worker's
prune loop gains extra pruners, and `interactions_seen` rows older than seven
days are deleted, since Discord retries within seconds.

## Consequences

- Recording and acting commit together, so a crash between them cannot lose a
  command or act on it twice.
- `INSERT`'s row count is not trusted with psycopg; `RETURNING` is.
