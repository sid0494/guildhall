# 0042. A structural test checks every fixture for allowlisted fields and fake IDs

Status: Accepted (2026-11-10)

## Context

The scrubber ([ADR 0041](0041-fixtures-are-trimmed-to-an-allowlist-with-fake-ids.md))
is code, and code has bugs; a hand edit can also add a field. The working
agreement asks for a test or script that checks scrubbing. The check must run
in CI, where no real IDs are available.

## Options considered

- **A structural test:** checks that only allowlisted fields are present and
  that every ID field holds a fake. Needs no secrets, so it runs everywhere,
  but it only knows what fake looks like.
- **Add a local denylist test:** the recorder saves the real IDs it replaced
  to a git-ignored file, and a test searches every fixture for them, skipped
  where the file is absent. Catches a real ID anywhere, at the cost of a local
  file and a guard that it stays ignored. It mostly protects the owner's own
  IDs, which the owner does not consider sensitive.
- **A gitleaks rule with the real IDs:** its configuration is committed, so it
  would publish the IDs it protects.

## Decision

`tests/unit/test_recorded_fixtures.py` checks that exactly the declared
fixture files exist, that each holds only the paths its shape allows, and that
every `account_id` and `match_id` is in the fake ranges (or a null account).
It computes paths and ID values itself rather than reusing the recorder's
trimming and pseudonym code, so a bug there cannot hide itself. It also pins
what later tests rely on: the unparsed and parsed fixtures are one listed
match, before and after parsing, with the recorded account in it once.

## Consequences

- Runs in pre-commit and CI with the other unit tests.
- A real ID inside an allowlisted field that is not an ID field would not be
  caught; the allowlist holds no such field today, and adding fields to it is
  reviewed.
