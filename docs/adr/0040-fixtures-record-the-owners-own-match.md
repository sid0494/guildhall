# 0040. Fixtures record the owner's own latest unparsed match

Status: Accepted (2026-11-10)

## Context

Gank Report needs four recorded responses: an account's match list (for
`poll_matches`), a match before it is parsed, the response to a parse request,
and the same kind of match after parsing. OpenDota usually parses an ordinary
match only when asked, so a recorder can follow the plug-in's own path:
fetch, request a parse, wait, fetch again.

## Options considered

- **The owner's own matches:** the most realistic data (the owner's bracket
  and game modes, anonymous players with a null `account_id`), and it checks
  that the owner's Dota profile exposes public match data, which the plug-in
  depends on. The owner's identity passes through the scrubber.
- **A random public match (`/publicMatches`):** never linked to the owner, but
  still real strangers' IDs; the match list must come from a stranger's
  account, and "my player" becomes an arbitrary slot.
- **A professional match:** already parsed, so no unparsed version, and full
  of team, captain and league fields an ordinary match lacks.

## Decision

Record the owner's own most recent unparsed match, from the account in
`GUILDHALL_FIXTURE_ACCOUNT_ID` (`.env`). The recorder fetches the match list
(limit 20), picks the newest match without a `version` (OpenDota sends
`null` in the list and leaves the field out of an unparsed match), fetches it,
requests a parse, then polls every 30 seconds for up to 15 minutes until the
parsed version arrives. The unparsed and parsed fixtures are one match.

## Consequences

- Tests replay the parse-retry path on one consistent match.
- Recording fails with a clear message if no matches are visible (the privacy
  setting) or the 20 most recent are already parsed (play another match).
- Some matches never parse; the recorder then gives up, and is run again
  later.
