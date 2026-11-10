# 0039. Recorded fixtures are response bodies written by a recording script

Status: Accepted (2026-11-10)

## Context

Gank Report's parsing and metric code should be tested against what OpenDota
really sends, but tests must never call the live API: it would make them slow
and flaky, spend the free tier's daily budget, and break CI whenever OpenDota
is down. The spec calls for recorded fixtures: real responses saved once and
replayed. Plug-ins see upstream responses only as the contract's
`HttpResponse` ([ADR 0035](0035-plugin-http-contract.md)), and the
repository is public.

## Options considered

- **VCR-style cassettes (`vcrpy`, `pytest-recording`):** the library records
  a test's real HTTP calls on its first run and replays them after. No
  recording code to write, but a cassette stores the whole request, so the
  URL (`/players/<account id>/matches`) and headers are saved and must be
  scrubbed through the library's filters; a missing cassette quietly makes a
  live call unless the record mode is pinned; and a cassette is tied to the
  order of one test's calls.
- **Plain JSON response bodies written by a script run by hand:** recording is
  a separate, reviewed step; files hold only bodies, and one file can serve
  unit tests, contract-suite examples and a fake OpenDota. We write and
  maintain the recorder, and nothing re-records automatically.

## Decision

Plain JSON bodies in `tests/fixtures/opendota/`, one per case the plug-in
handles, written by `scripts/record_opendota.py`. Tests serve them through a
fake `ctx.http`. Tests import the recorder's shapes and constants by name
(`pythonpath` in pytest, `mypy_path` in mypy).

## Consequences

- No test can reach OpenDota by accident: there is no record mode.
- Re-recording is a deliberate command whose result is reviewed as a diff.
- The recorder is development tooling: it lives outside `src/`, so `deptry`
  does not hold it to the runtime dependencies, but it is linted, type checked
  and tested like the rest.
