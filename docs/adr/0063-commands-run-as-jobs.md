# 0063. Every command is deferred and runs on the worker as a job

Status: Accepted (2026-11-10)

## Context

Discord expects an answer within three seconds. Some commands only read a row;
others call upstream APIs or start long work. The worker already gives jobs
dedupe, retries with backoff, rate limits, a timeout, typed errors and
dead-lettering; the api runs no plug-in code.

## Options considered

- **Every command deferred, run as a job:** one path reusing every platform
  guarantee; replies take a second or two, shown as "thinking...".
- **Quick commands inline in the api, slow ones as jobs:** instant answers for
  reads, but two execution paths, plug-in code under a hard three-second
  deadline in the api, and a slow query becomes a Discord error rather than a
  retry.
- **Everything inline:** impossible for commands that call upstream APIs.

## Decision

The api records the interaction and queues a command job through the outbox
in one transaction ([ADR 0061](0061-interactions-endpoint-flow.md)), then
answers with a deferred response, private if the command is. The job's type
is `command:<path>` (a colon cannot appear in a plug-in's job types), its key
`<plug-in>:command:<interaction id>`, and its payload the path, caller, raw
options, application ID, interaction token (never logged) and privacy. The
runner validates the options against the command's model, looks up the
caller's linked account, and runs the handler with the same timeout, error
handling and outcome checks as a job. A command no plug-in offers any more is
answered at once, privately, without a job.

## Consequences

- The interaction token travels in the outbox and on the queue: a 15-minute
  credential that can edit only its own reply.
- The api loads the enabled plug-ins to route commands, so it needs
  `GUILDHALL_PLUGINS` like the worker.
