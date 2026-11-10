# 0058. The api runs on FastAPI and uvicorn

Status: Accepted (2026-11-10)

## Context

The api serves a few routes: the interactions endpoint and health checks. Its
interactions route must verify a signature over the raw request bytes before
parsing anything. An ASGI framework handles requests; an ASGI server listens
on the port and speaks HTTP.

## Options considered

- **FastAPI:** the spec's stack choice, on Starlette and pydantic; dependency
  injection gives tests a seam; its validation and OpenAPI docs do not fit a
  raw-body endpoint defined by Discord.
- **Starlette alone:** leaner, no dependency injection, wiring by hand.
- **Litestar or aiohttp:** capable, less common, nothing we need beyond
  FastAPI.
- **Servers:** uvicorn, the reference ASGI server, with or without its
  `[standard]` speed-ups; Hypercorn or Granian, for HTTP/2 or a Rust core.

## Decision

FastAPI and plain uvicorn, without extras: a few requests a day need no speed
and a smaller image is better. `create_app(settings, interaction_log)` builds
the app; its docs routes are off, because the URL is public. The
`guildhall-api` command runs uvicorn without its own logging setup and
without an access log, so every line goes through the platform's structured
logging. In compose, the api is a service from the same image as the worker.

## Consequences

- The interactions route reads `request.body()` itself, never a parsed body.
- FastAPI brings Starlette and a few small libraries with it.
