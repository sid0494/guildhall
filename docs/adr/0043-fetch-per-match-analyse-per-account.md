# 0043. Fetch each match once; analyse it per linked account

Status: Accepted (2026-11-10)

## Context

The spec allows a few friends to link accounts later and says the design
should not prevent it. Its first data model did: `gank_report_matches` held one
row per match, metrics were keyed by match alone, and the single job key
`gank_report:match:<id>` made a second linked player's fetch of the same match
a no-op, so that player was never recorded. One OpenDota response holds all
ten players, so fetching once is natural; the question is who gets credited,
and when. Plug-ins cannot read `linked_accounts`
([ADR 0030](0030-plugin-reads-through-a-checked-fetch.md)), and dedupe keys,
which appear in logs, carry no personal IDs
([ADR 0038](0038-scheduled-runs-and-linked-accounts.md)).

## Options considered

- **One account, as first specified:** simplest now; supporting friends later
  means changing keys and primary keys and migrating data.
- **A fetch that credits every linked player in the match:** needs the plug-in
  to see linked accounts (a contract addition), and an account linked after
  the match was fetched is still never recorded, because the key has taken
  effect.
- **One job per match and account that fetches and analyses:** independent and
  simple, but friends in one match fetch it once each, and recomputing metrics
  needs a second path later.
- **A fetch per match, then an analysis per account from the archive:** no
  duplicate calls, late links work, and the analysis is the recompute path the
  archive exists for; costs a second job type and a wait.

## Decision

- `fetch_match(match_id)`, keyed `gank_report:match:<id>`, owns everything
  upstream: OpenDota calls, the parse wait, the archive, and a match-level
  row. It knows nothing about who played.
- `analyze_match(match_id, account)`, keyed
  `gank_report:analyze:<id>:<link id>`, waits for the match row, reads the
  archived response with `ctx.archive.get` and records that account's part.
  Metrics and the `gank_report.match_analyzed` event belong here (B3).
- `poll_matches` returns both jobs for each new match, because an outcome that
  retries cannot also ask for jobs.
- `LinkedAccount` gains `id`, the link's row ID, for the analysis key. It is
  required: no scheduled run with an account has been produced yet (`/link`
  arrives later), so no queued payload can lack it, and `contract_version`
  stays 1.

## Consequences

- Each match costs one fetch however many linked players were in it.
- A metric change re-runs analyses over archived matches, with no upstream
  calls.
- An analysis usually runs before its fetch finishes and waits
  ([ADR 0046](0046-analysis-waits-for-the-fetch.md)).
