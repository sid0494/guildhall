# 0041. Fixtures are trimmed to an allowlist, with fake match and account IDs

Status: Accepted (2026-11-10)

## Context

A parsed match is about 326 KB (550 KB pretty-printed, over the 500 KB
pre-commit limit) with 152 fields per player. Many identify people: account
IDs, persona names, party IDs, login times, chat text and word counts, replay
salts, and the match ID itself, which leads to every player in the match. The
working agreement treats all of these as sensitive and requires recorded
fixtures to be scrubbed before they are committed.

## Options considered

- **Replace known identifying fields, keep everything else:** the true shape
  is kept, so any field is available to future metrics; but it fails open (a
  field OpenDota adds later passes through unscrubbed) and a 550 KB file
  cannot be reviewed before a commit.
- **Trim to an allowlist of the fields Gank Report uses, then fake the IDs
  that remain:** fails closed and gives files of about 9 KB that can be read in
  full; a new metric that needs a field not on the list means adding it and
  re-recording.
- **Classify every field as keep, replace or drop, and refuse unclassified
  ones:** full shape and fails closed, but about 210 fields to classify,
  several with dynamic keys, and the files stay too large to review.

## Decision

Each fixture has a shape in `scripts/record_opendota.py` naming the fields to
keep. Trimming never adds a field, so an unparsed match still lacks `lh_t`.
A kept value must be a scalar or a list of scalars; a nested object is kept
only through its own shape, so new fields inside it are dropped too. Every
`account_id` and `match_id` is then replaced, the same real ID always getting
the same fake: accounts from 1001 (the recorded account is always 1001),
matches from 5001. A player who hides their match data has a null
`account_id`, or none at all in an unparsed match; either stays as it is. Strangers'
IDs are faked as well as the owner's.

## Consequences

- No name, chat, party or replay field ever reaches disk.
- Fake IDs are visibly different from real ones (9 or 10 digits).
- A metric reading a field that was not kept sees it missing, so its test
  fails loudly; the fix is one shape entry and a re-recording.
- Start times are kept: the plug-in needs them.
