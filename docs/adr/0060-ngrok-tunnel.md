# 0060. Locally, an ngrok tunnel on a free static domain exposes the api

Status: Accepted (2026-11-10)

## Context

Discord must reach the api over public HTTPS, but the development stack runs
behind a home router. Only interactions need this: posts are outbound calls.
Each time the public URL changes, it must be pasted into the developer
portal, where Discord validates it again. The stack runs locally often,
including between AWS sessions.

## Options considered

- **A cloudflared quick tunnel:** no account, but a new random URL each run.
- **ngrok's free plan:** an account and an authtoken (a secret), and one free
  static domain, so the URL is set once.
- **A Cloudflare named tunnel:** production-grade, but needs a domain, about
  $10 a year.
- **Tailscale Funnel:** a stable URL, with more concepts to learn for one URL.

## Decision

ngrok, as a compose service in the `tunnel` profile, pinned by tag and
digest, running `http api:8000 --url https://<domain>` once the api is
healthy. `GUILDHALL_NGROK_AUTHTOKEN` and `GUILDHALL_NGROK_DOMAIN` live in
`.env`, with empty defaults so compose loads without them. The agent's
inspector is published on `127.0.0.1:4040`.

## Consequences

- The Interactions Endpoint URL is set once in the portal.
- Commands that touch `ngrok` must name both the `app` and `tunnel` profiles.
- If ngrok's free plan changes, a cloudflared quick tunnel replaces the
  service without code changes.
- The public URL reaches only the api, whose interactions route acts on
  nothing without a valid signature.
