# 0007. Keep dependencies current with Dependabot

Status: Accepted (2026-11-07)

## Context

`uv.lock` and the SHA-pinned actions in CI make builds reproducible, but they
also freeze known vulnerabilities in place until someone updates them.
Updates should arrive as pull requests, so they pass through the same CI gate
as any other change, without creating more review work than one person can
keep up with.

## Options considered

Tool:

- **Dependabot:** built into GitHub; vulnerability alerts, automatic security
  fix pull requests, and scheduled version updates configured in one YAML
  file. Supports `uv.lock` and updates SHA-pinned actions together with their
  version comments. Does not update pre-commit hook revisions.
- **Renovate:** more configurable and also covers pre-commit hooks, but runs
  as a third-party GitHub App with repository access and a larger
  configuration surface.
- **Alerts and security fixes only:** least noise, but dependencies age until
  a vulnerability forces a large jump.

Cadence and grouping:

- **One pull request per dependency, weekly:** easy to see which update broke
  CI, but noisy for a single maintainer.
- **Grouped, weekly:** small, regular batches.
- **Grouped, monthly:** at most one pull request per ecosystem per month;
  larger batches, so a failure takes slightly longer to attribute.

## Decision

- Dependabot vulnerability alerts and security updates are enabled in the
  repository settings; security fix pull requests arrive as soon as an
  advisory is published.
- `.github/dependabot.yml` schedules monthly version updates for the `uv` and
  `github-actions` ecosystems, each grouped into a single pull request.
- A 7-day cooldown applies to version updates: a release is only proposed
  once it has been public for a week, by which time most malicious or broken
  releases have been withdrawn. Security updates are not delayed.

## Consequences

- At most two routine update pull requests a month, each checked by CI before
  it can be merged.
- If a grouped update fails CI, the failing package is found from the CI log
  and can be held back with an `ignore` entry until it is fixed.
- Pre-commit hook revisions (`uv run pre-commit autoupdate`) and the gitleaks
  version and checksum in the CI workflow remain manual updates.
