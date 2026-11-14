# 0075. Gank Report has no end-to-end test; a real match accepts M2

Status: Accepted (2026-11-12)

## Context

M2 is done when a match the owner plays is reported in Discord on its own.
The end-to-end stack ([ADR 0027](0027-end-to-end-tests.md)) runs only the
synthetic plug-in. Running Gank Report there would need fakes for OpenDota
and Discord, whose base URLs are constants today, plus short poll and parse
timings so a run takes seconds rather than the 15-minute poll and 5- and
10-minute parse waits.

Gank Report is already covered below that level: unit tests for metrics,
account IDs and rendering; contract tests against the shared fakes; recorded
fixtures; and integration tests against Postgres for analysis, linking,
replies and posting.

## Options considered

- **A fake upstream as a compose service:** a small FastAPI app serving the
  recorded fixtures and recording Discord posts. Checks the wiring between
  services on every PR; costs configurable base URLs and timings, a new
  service and a slower end-to-end run.
- **An off-the-shelf mock server (WireMock, MockServer):** the same coverage,
  with a Java image and a mapping language for the stateful "unparsed until
  asked to parse" behaviour.
- **No Gank Report end-to-end test:** rely on the lower layers, and accept M2
  by playing a real match against the development stack.

## Decision

No Gank Report end-to-end test. The lower layers cover its behaviour, a
broken report costs nothing beyond a missing Discord post, and the real-match
check exercises the actual upstreams, which no fake can.

## Consequences

- A wiring mistake between services (a missing setting in compose, a job
  type the worker does not route) is caught only by running the stack, not
  by CI.
- The upstream URLs stay constants.
- Revisit when the mock upstream server for the failure drills is built: it
  can serve the same fixtures, and Gank Report could then join the
  end-to-end tests cheaply.
