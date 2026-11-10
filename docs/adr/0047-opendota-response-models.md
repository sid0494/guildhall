# 0047. OpenDota responses are read through models; bad ones fail permanently

Status: Accepted (2026-11-10)

## Context

`ctx.http` raises `RetryableError` for `5xx`, timeouts and connection
failures, defers on `429`, and returns every other status for the plug-in to
judge ([ADR 0035](0035-plugin-http-contract.md)). The runner treats any other
exception from a handler as retryable. The recorded fixtures showed shapes
that differ from the obvious assumptions: an unparsed match has no `version`,
and a player who hides their match data has no `account_id` in it.

## Options considered

- **Dict access:** a changed field raises `KeyError`, which the runner retries
  five times, calling OpenDota each time, before dead-lettering with an
  unhelpful reason.
- **Pydantic models of the fields read:** one validation step, typed values,
  and a precise failure.
- **The runner treating any `ValidationError` as permanent:** a platform change
  that would also misreport the plug-in's own bugs as upstream problems.

## Decision

`plugins/gank_report/opendota.py` holds frozen models of just the fields the
plug-in reads, ignoring the rest, with the optional fields the fixtures
showed. `parse(model, body)` raises `PermanentError` naming field locations
and messages, never values, and cuts the chain to the validation error
(`from None`), whose text includes the input. A `404` fails permanently as
"not found", and any other non-success status fails permanently with the
status. Validating a full 282 KB match takes about 0.5 ms, so it runs on the
event loop.

## Consequences

- A real change in OpenDota's responses dead-letters on the first attempt,
  with the fields named.
- A transient malformed `200`, if it happens, is dead-lettered and needs a
  manual redrive instead of retrying.
- Metric code (B3) reads typed fields; each new field read is added to a model.
