# 0010. Enforce the contract with shared checks, a parametrised suite and a synthetic plug-in

Status: Accepted (2026-11-08)

## Context

The contract's types catch shape mistakes, but several rules concern values:
plug-in and job type names must be identifiers, job type names unique,
schedules must name declared job types, and every dedupe key and event type a
handler returns must be namespaced by the plug-in's name. The spec requires a
contract test suite every plug-in must pass, and failure drills and a load
check that run plug-in jobs through the real worker on kind.

## Options considered

Who enforces value rules:

- **The test suite only:** checked in CI; the running platform trusts
  plug-ins, so a bug the tests missed reaches production unchecked.
- **Shared check functions used by the platform and the suite:** the registry
  rejects bad declarations at startup, the worker checks every outcome, and
  CI runs the same functions.

How a plug-in joins the suite:

- **A base test class per plug-in:** explicit, but a plug-in without its test
  class is silently unchecked.
- **One parametrised suite over every plug-in:** each plug-in supplies example
  payloads; coverage is automatic.

Where the fake plug-in lives:

- **Under `tests/`:** never ships, but drills and the load check need a
  plug-in inside the container image.
- **A synthetic plug-in under `src/`, disabled by default:** one job type whose
  payload describes what to do (sleep, wait, fail, fan out, emit events), so
  every drill and the load check use it without new code.

## Decision

- `guildhall.platform.checks` provides `check_declarations(plugin)` and
  `check_outcome(plugin, outcome)`, each returning every problem found. The
  registry will run the first at startup and the worker the second on every
  outcome. Plug-ins never import them.
- `tests/contract/test_plugin_contract.py` runs every check against every
  plug-in listed in `CASES`, with example payloads for each job type. A fake
  platform runs each example after a JSON round trip of its payload; the
  outcome must pass `check_outcome`, or the handler must raise a typed error.
  A guard test fails if any plug-in class under `guildhall.plugins` has no
  case.
- `guildhall.plugins.synthetic` is a payload-driven plug-in for end-to-end
  tests, drills and load checks. It calls no upstream API and is enabled only
  through config. `fail="crash"` raises an untyped exception on purpose.

## Consequences

- A plug-in with bad declarations stops the platform from starting, with a
  message listing every problem.
- An outcome that breaks the namespacing rules is rejected at runtime even if
  the suite missed it; the worker will dead-letter the job with the problems
  as its reason.
- Every new plug-in must add examples to `CASES`; the guard test enforces it.
- The synthetic plug-in ships in the image as dormant code.
