# 0044. Ask for a parse on every unparsed fetch but the last

Status: Accepted (2026-11-10)

## Context

Several metrics need OpenDota to have parsed the match's replay. It does not
parse ordinary matches unless asked (none of the 20 recent matches recorded
in B1 were parsed), parsing usually takes 1 to 5 minutes, and some matches
never parse. The spec gives up after 3 attempts. `job.attempt` counts every
run, failed ones included, and a retrying outcome carries no state to the
next attempt: the payload is sent again unchanged
([ADR 0016](0016-worker-job-lifecycle.md)). A retry wait is at most 15
minutes. How OpenDota treats a repeated parse request was not verified.

## Options considered

- **Ask once, on attempt 1:** politest; but if attempt 1 fails before asking,
  or OpenDota drops the request, the remaining attempts wait for nothing.
- **Poll the parse job (`GET /request/{jobId}`) instead of the match:** smaller
  responses and early failure detection, but the job ID from attempt 1 cannot
  reach attempt 2.
- **Ask on every unparsed fetch but the last:** at most 3 fetches and 2
  requests per match; a lost request or failed attempt is made up for.

## Decision

Fetch the match. Parsed (`od_data.has_parsed`; an unparsed match has no
`version` field at all) means finish: archive the body and record the match.
Unparsed on attempt 1 or 2 means request a parse and retry in 5, then 10
minutes. Attempt 3 finishes unparsed. The parse request is best effort: an
error from it does not fail the job, since the next attempt asks again and the
last finishes anyway. The delays live in `PARSE_RETRY_DELAYS`, a constant of
the plug-in, not a setting.

## Consequences

- A match is recorded at most about 15 minutes after its fetch starts.
- Only the final response, parsed or not, is archived; retrying outcomes
  cannot archive ([ADR 0031](0031-raw-archive-declared-in-the-outcome.md)).
- A match that parses after its last attempt stays `parsed = false`. A later
  sweep that fetches such matches again is a tracked follow-up; the match row
  is an upsert so that it can.
