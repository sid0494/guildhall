# 0050. Upstream call logs and errors mask all-digit path segments

Status: Accepted (2026-11-10)

## Context

The platform logs every upstream call with its method, host and path, and
the same text appears in retryable and throttled errors, and so in
dead-letter reasons; query strings and headers are left out because API keys
travel there ([ADR 0035](0035-plugin-http-contract.md)). OpenDota puts the
player in the path: `/api/players/<Dota account ID>/matches`, called every 15
minutes per linked account. Dedupe keys were kept free of personal IDs
precisely because they appear in logs
([ADR 0038](0038-scheduled-runs-and-linked-accounts.md)).

## Options considered

- **Accept it:** logs are private, but this undoes ADR 0038's reasoning, and
  on AWS the IDs would reach CloudWatch.
- **Let the plug-in say how a call is logged** (`log_as=...`): precise, but a
  contract change every plug-in must remember, and forgetting it leaks
  silently.
- **Mask all-digit path segments in the platform:** generic and on by default;
  loses the match ID in `/matches/{n}` lines, which the job's dedupe key,
  bound to every log line, already carries.

## Decision

`platform/http.py` writes each all-digit path segment as `{n}`, in the
`upstream call` log line and in the text `describe()` gives errors. Other
segments are kept.

## Consequences

- No numeric ID in a path reaches a log line or a dead-letter reason.
- An identifier that is not all digits (such as a Steam vanity name) would not
  be masked; no upstream we use puts one in a path.
