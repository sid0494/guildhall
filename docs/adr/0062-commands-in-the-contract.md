# 0062. Plug-ins declare slash commands in the contract

Status: Accepted (2026-11-10)

## Context

Gank Report needs `/match last`, `/trend` and `/focus`; Side Quest will add its
own. The platform must register commands with Discord, route each invocation
to its plug-in and deliver the reply, while plug-ins stay unaware of Discord.
Plug-ins cannot read `linked_accounts`
([ADR 0030](0030-plugin-reads-through-a-checked-fetch.md)), yet most commands
are about the caller's account. With deferred replies, Discord fixes whether
a reply is private when the first answer is sent, before any plug-in code
runs.

## Options considered

- **Naming:** flat names (`/match_last`) or a path of up to two levels
  (`/match last`), merged by the platform into Discord's subcommands.
- **Options:** Discord's option objects written by hand, or a pydantic model
  whose fields become options.
- **The caller's account:** looked up by the handler (impossible without
  reading platform tables) or supplied by the platform.
- **The result:** the job `Outcome` with an optional reply, or a separate
  `CommandOutcome` whose reply is required and which cannot retry.
- **Privacy:** a flag on the reply (`Message.ephemeral`), a reserved option the
  api reads on every command, or a property of the command.

## Decision

`SourcePlugin.commands()` returns `Command(path, description, options,
handler, private=False)`. Paths have one or two names of lower-case
characters; `/link` and `/guildhall` are reserved for the platform; each
top-level name belongs to one plug-in. The options model's `str`, `int`,
`float` and `bool` fields become options (optional with a default or `None`;
`ge`, `le` and length limits carried over; a description required); anything
Discord cannot express is a declaration error. A handler receives
`Invocation(discord_user_id, account, options)`, where `account` is the
caller's link for that plug-in, and returns `CommandOutcome(reply, writes,
jobs, events)`. `Message` holds the content only and serves posts too.
Privacy belongs to the command: the api reads it from the declaration when it
defers. Group descriptions are made from the group's name.

## Consequences

- The declaration checks validate commands at startup and in the contract
  suite, and the registry checks owners across plug-ins.
- A user-facing problem ("that is not an account ID") is a normal reply; an
  error raised by a handler gets a generic reply.
- Privacy first sat on `Message`; it moved to `Command` before merging, once
  it was clear Discord decides it at the deferred answer.
