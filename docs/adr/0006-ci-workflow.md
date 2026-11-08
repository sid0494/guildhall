# 0006. CI runs the pre-commit hooks and a full-history secret scan

Status: Accepted (2026-11-07)

## Context

CI is the merge gate: lint, type checks and tests must pass on every pull
request. Local pre-commit hooks can be skipped with `--no-verify` or never
installed on a fresh clone, so CI must repeat them. The local gitleaks hook
only sees staged changes, so a secret committed while hooks were skipped
needs a server-side check. Third-party actions run with access to the
repository and the workflow token, so how they are referenced is a
supply-chain concern.

## Options considered

How CI runs the checks:

- **One step per tool** (`ruff check`, `mypy`, `pytest`): clearer step list
  in the GitHub UI, but the list of checks is kept in two places and drifts.
- **`pre-commit run --all-files`:** CI runs exactly the hooks defined in
  `.pre-commit-config.yaml`, including the hygiene hooks; one step in the UI,
  with one line per hook in its log.

How actions are referenced:

- **Version tags** (`@v7`): readable and pick up fixes automatically, but a
  tag can be moved to different code. In March 2025 the compromised
  `tj-actions/changed-files` action had its tags re-pointed at code that
  leaked CI secrets.
- **Commit SHAs** with the release in a comment: immutable; updates are a
  deliberate change, which Dependabot can automate.

Server-side secret scan:

- **gitleaks-action:** convenient, but by default scans only the commits in
  the push or pull request.
- **gitleaks release binary, checksum verified, scanning the full history:**
  catches anything ever committed, and the scan takes well under a second at
  this repository's size.

## Decision

- One workflow, `.github/workflows/ci.yml`, triggered by pull requests and
  pushes to `main`, with two jobs:
  - **Lint, type check and tests:** installs uv (version pinned), runs
    `uv sync --locked`, then `uv run --locked pre-commit run --all-files`.
    The gitleaks hook is skipped there because the next job covers it.
    Pre-commit environments are cached by the config file's hash.
  - **Secret scan (full history):** checks out every commit and runs a
    checksum-verified gitleaks binary over all of them, at the same version
    as the pre-commit hook.
- Every third-party action is pinned to a full commit SHA, with the release
  in a trailing comment.
- The workflow token is read-only (`permissions: contents: read`), checkout
  does not persist credentials, jobs have timeouts, and a newer run on the
  same branch cancels the older one.

## Consequences

- Adding a hook to `.pre-commit-config.yaml` adds it to CI automatically.
- Action and gitleaks versions only change through a reviewed commit; the
  gitleaks version and checksum in the workflow must be updated together with
  the pre-commit hook's revision.
- The two job names are the status checks the `main` branch ruleset will
  require.
- GitHub Actions minutes are free and unlimited for public repositories.
