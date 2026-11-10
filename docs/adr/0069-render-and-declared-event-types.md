# 0069. Plug-ins render their events in a plain function and declare event types

Status: Accepted (2026-11-10)

## Context

Events wait in `outbox_events` as JSON until the worker posts them. Rendering
happens for many events in a row; a plug-in that queried a database or called
an API while rendering would stall or fail delivery for every plug-in. To
render, the platform must rebuild a stored event as the plug-in's model.

## Options considered

- **A synchronous `render(event)` with no context:** nothing to await and no
  services to use.
- **An `async render` with a restricted context:** invites awaiting, and the
  restriction must be maintained.
- **Rendering when the event is created**, storing the finished message:
  events become formatted text, so a formatting fix cannot apply to events
  already waiting, and other readers lose the data.

## Decision

`SourcePlugin.event_types()` lists the plug-in's `Event` classes, each fixing a
namespaced `type`; `render(event) -> Message | None` turns one into a message,
None meaning "do not post". The declaration checks require the declared types
to be namespaced and unique and `render` not to be a coroutine; at runtime a
handler may emit only declared event types. The contract suite renders an
example of every declared event type with sockets blocked, so a render that
reaches for the network fails the suite. The dispatcher runs `render` in a
thread under a time limit, and treats a failure as that event's alone.

## Consequences

- Everything a post shows travels in the event (ADR 0055).
- Fields added to an event within a schema version need defaults, so events
  already stored still validate.
