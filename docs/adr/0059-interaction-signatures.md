# 0059. Interaction signatures are verified with `cryptography`

Status: Accepted (2026-11-10)

## Context

Discord signs each interaction's timestamp followed by its raw body with
Ed25519 and sends the signature and timestamp in headers; the application's
public key verifies it. Discord refuses to use an endpoint that accepts badly
signed requests, and tests this when the URL is saved. Hand-written
cryptography is how forged requests get accepted.

## Options considered

- **`cryptography` (pyca):** the most widely deployed Python cryptography
  library, actively maintained, covering any later needs; a larger wheel.
- **PyNaCl (libsodium):** what Discord's documentation uses; released less
  often and limited to NaCl primitives.
- **A pure-Python Ed25519 package:** no compiled code, but slower, less
  scrutinised and not constant-time.
- **Rejecting old timestamps** (a common replay defence): Discord documents no
  tolerance, and a drifting local clock (WSL after sleep) would refuse real
  requests, which can get the endpoint disabled.

## Decision

`SignatureVerifier` uses `cryptography`'s `Ed25519PublicKey`. A key that is
not 32 bytes of hex fails at startup. A missing, non-hex or wrong-length
header is an invalid signature, answered with `401`, never an error. Nothing
from a rejected request (body, headers, signature) is logged. Timestamps are
not checked for freshness: a replayed command is stopped by deduplication
([ADR 0061](0061-interactions-endpoint-flow.md)) and a replayed `PING` is
harmless.

## Consequences

- Tests sign requests with a keypair generated for each run; no real key is in
  the repository. The end-to-end stack uses the public half of a keypair whose
  private half was discarded. Gitleaks' `discord-api-token` rule matches any
  64-hex value near "discord", public keys included, so that one line carries
  an explicit `gitleaks:allow` with the reason beside it.
