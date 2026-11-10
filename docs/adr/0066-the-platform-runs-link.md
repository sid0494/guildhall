# 0066. The platform runs `/link`; plug-ins declare and verify account kinds

Status: Accepted (2026-11-10)

## Context

Linking connects a Discord user to their account in a plug-in's source, which
the scheduler fans out over and every command reads. The links live in
`linked_accounts`, a platform table plug-ins cannot read or write
([ADR 0030](0030-plugin-reads-through-a-checked-fetch.md)); only the plug-in
knows what makes an account valid. A follow-up job (such as a backfill) needs
the link's row ID for its keys, which exists only once the row is stored.

## Options considered

- **The platform runs `/link`; plug-ins declare a kind and a check:** one
  consistent command, plug-ins never write platform tables.
- **Plug-ins write links through a new `ctx.accounts.link`:** flexible, but
  write access to a platform table, and every plug-in rebuilds the same flow.
- **No check:** a typo or a private profile shows up only as reports that
  never come.
- **Re-linking:** replace the account, or refuse until an unlink.

## Decision

`SourcePlugin.account_link()` returns `AccountLink(kind, description, verify,
on_link)`. The platform registers `/link <kind> account:<id>` for each kind
(always private) and runs it as a command job: `verify(LinkRequest, ctx)`,
which sees the caller's current link as `previous`, returns `Accepted(id)`
with the ID to store or `Rejected(reason)`, shown privately. On acceptance,
one transaction claims the job, upserts the link on (plug-in, Discord user),
keeping its row ID and updating the account and `linked_at`, queues the
`on_link` job with the stored `LinkedAccount`, and queues the reply ("Linked
your dota account 123, replacing 456."). The follow-up's key is the `/link`
invocation's, so linking again runs it again. There is no unlink. The checks
require `on_link` to name a job type taking `LinkedAccount`, and each kind to
have one owner.

## Consequences

- Plug-ins validate accounts with their own rate budget and context.
- Re-linking keeps history: plug-in rows keyed by the external account stay
  with that account.
- Removing a link would need an unlink command, deliberately left out.
