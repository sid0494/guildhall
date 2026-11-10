# 0065. Commands are registered by a command, on the test server

Status: Accepted (2026-11-10)

## Context

Discord shows only commands it has been told about. A bulk overwrite (`PUT
.../commands` with the full list) makes Discord's set match the list exactly.
It needs the bot token, which the api does not otherwise need. Guild commands
apply instantly to one server; global commands reach every server the bot is
in.

## Options considered

- **When:** a command run by hand (and later by the deploy pipeline), the api
  at every startup (it would hold the bot token; replicas race; startup would
  depend on Discord), or the worker at startup (the same race and
  dependency).
- **Where:** the test server only, or global.

## Decision

`guildhall-register-commands` builds the list from the enabled plug-ins and
overwrites the test server's commands; `--dry-run` prints the JSON instead of
calling Discord. `GUILDHALL_DISCORD_APPLICATION_ID`,
`GUILDHALL_DISCORD_GUILD_ID` (personal, so only in `.env`) and
`GUILDHALL_DISCORD_BOT_TOKEN` (a secret) configure it. Requests identify
themselves as `DiscordBot (<url>, <version>)`, as Discord requires; errors name
Discord's reason, never the token.

## Consequences

- After adding, changing or removing a command, the command is run again.
- Serving friends' servers means one registration per server, or moving to
  global commands.
