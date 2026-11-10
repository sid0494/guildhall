# 0057. Discord reaches Guildhall through HTTP interactions, not a gateway

Status: Accepted (2026-11-10)

## Context

Guildhall answers slash commands and posts reports. Discord offers two ways
in: an HTTP interactions endpoint, which Discord calls once per interaction,
and the gateway, a long-lived WebSocket that streams subscribed events. The
platform is built from stateless processes that can run as several replicas.

## Options considered

- **HTTP interactions:** a plain stateless web service that scales and
  restarts freely; it receives only interactions (commands, buttons), not
  messages, reactions or presence; it needs a public HTTPS URL.
- **A gateway bot:** receives every subscribed event and needs no public URL,
  but holds one stateful connection per shard: a second replica handles
  events twice, and a restart drops events unless the session is resumed.
- **Both:** a gateway for event-driven features and HTTP for commands; no
  planned feature needs gateway events.

## Decision

HTTP interactions, served by the api (`POST /interactions`). Proactive posts
use Discord's REST API with the bot token, from the outbox dispatcher.

## Consequences

- The api needs a public HTTPS URL: a tunnel locally
  ([ADR 0060](0060-ngrok-tunnel.md)), a load balancer on AWS.
- A feature that needs gateway events would add a separate gateway process.
