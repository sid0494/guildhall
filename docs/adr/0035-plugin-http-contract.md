# 0035. `ctx.http`: a narrow HTTP contract on httpx

Status: Accepted (2026-11-09)

## Context

Plug-ins call their upstream APIs through the platform, so every call can be
rate-limited (ADR 0034), throttling can defer the job (ADR 0033), and API keys
stay out of error text and logs. The contract decides what plug-ins see; the
platform decides which HTTP library sits underneath.

## Options considered

- **Hand plug-ins an `httpx.AsyncClient`** whose transport applies the limits:
  every feature of httpx, but the contract then depends on httpx, and a
  plug-in could build its own client and skip the limiter.
- **A narrow contract type:** `ctx.http.get(url, params=...)` and
  `.post(url, params=..., json=...)` returning the contract's own
  `HttpResponse` (status, lower-case headers, body bytes, `.json()`).
- **Library: aiohttp**, already installed through aiobotocore, or **httpx**,
  whose built-in `MockTransport` lets tests answer requests with no server.

## Decision

The narrow contract, on httpx:

- `Http` (protocol) and `HttpResponse` (a frozen dataclass; it never crosses
  the queue, so it is not a pydantic contract value) are in
  `guildhall.contract.http`. `response.body` is the bytes as received, ready
  for an `Archive`.
- The platform handles the transport: `429` defers the job (ADR 0033); `5xx`,
  timeouts and connection failures raise `RetryableError`; every other status,
  `404` included, is returned for the plug-in to judge.
- One shared `httpx.AsyncClient`: 10 s per request
  (`GUILDHALL_HTTP_TIMEOUT_SECONDS`), `User-Agent: guildhall/0.1` with no
  personal details, redirects not followed.
- Errors and the one `upstream call` log line per request name the method,
  host, path, status and duration only, never the query string or headers,
  where API keys go. The `httpx` and `httpcore` loggers are held at WARNING
  whatever the log level, because httpx logs every request's full URL at
  INFO; a test caught that leak.

## Consequences

- The HTTP library is a platform detail and can change without touching
  plug-ins. Handler tests fake `ctx.http` in a few lines.
- Plug-ins get GET and POST with query parameters and a JSON body; anything
  else (other methods, form bodies, custom headers such as API keys in a
  header) is added to the contract when a plug-in needs it.
- `Context` gained a field; plug-ins never build a `Context`, so
  `contract_version` stays 1.
