# 0001. Record architecture decisions

Status: Accepted (2026-11-07)

## Context

Guildhall is built in small steps over months, and the design spec describes
the system as it should be, not how it got there. Code shows what was built
but not why, or which alternatives were rejected. Without a written record,
past decisions get relitigated or silently reversed.

Two questions need answering: which format decision records use, and how they
are numbered. The design spec's "Decisions to record" table had pre-assigned
numbers 001–008 to decisions not yet made, while tooling decisions will be
made before any of them.

## Options considered

Format:

- **Nygard's original format** (Context, Decision, Consequences): short, but
  rejected options end up buried in the Context section or left out.
- **Full MADR** (decision drivers, considered options with pros and cons per
  option): thorough, but heavy enough that small decisions would go
  unrecorded.
- **Nygard's format plus an "Options considered" section**: keeps records
  short while making the rejected alternatives explicit.

Numbering:

- **Reserve 001–008 for the decisions listed in the spec**: keeps the spec
  table stable, but numbers no longer follow the order decisions were made.
- **Number each record when it is written**: numbers form a chronological
  log, which is the usual ADR convention.

Tooling: the `adr-tools` CLI was considered and rejected; it only copies a
template and increments a number, which does not justify another dependency.

## Decision

- Each decision is recorded as `docs/adr/NNNN-short-title.md`, using
  [template.md](template.md): Status, Context, Options considered, Decision,
  Consequences.
- Numbers are four digits, assigned in the order records are written.
- Accepted records are not rewritten. A changed decision gets a new record,
  and the old one's status becomes "Superseded by NNNN".
- Small decisions are recorded too.
- The design spec's decision table lists pending decisions without numbers
  and links each one to its record once it exists.

## Consequences

- Every pull request that makes a design decision includes its record, so the
  reasoning is reviewed together with the change.
- The spec and the records must be kept consistent: when a record changes the
  design, the spec is updated in the same pull request.
- Records are plain Markdown, readable on GitHub with no tooling.
