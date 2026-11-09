# 0020. Structured logging with structlog and context variables

Status: Accepted (2026-11-09)

## Context

The spec asks for JSON logs with `plugin`, `job_type`, `dedupe_key` and
`attempt` on every line. "Every line" includes lines a plug-in writes inside
its handler and lines from libraries such as SQLAlchemy and aiobotocore,
none of which should have to pass the job's fields along. The worker process
(next) runs several jobs at once in one event loop, so the current job cannot
be held in a global: concurrent jobs would overwrite each other's fields.

## Options considered

- **Standard `logging` with our own JSON formatter and a context filter:** no
  dependency, but we write, test and maintain the formatter, the context
  handling and a readable development format ourselves.
- **structlog:** log calls produce dictionaries that pass through a chain of
  small functions (add a timestamp, merge context, render). Context variables,
  a bridge for standard `logging` records, JSON and console renderers and test
  helpers are built in. One dependency and some setup to learn.
- **python-json-logger:** only a JSON formatter for standard `logging`; the
  context handling would still be ours.
- **loguru:** pleasant API, but one global logger with its own interface;
  standard `logging` records need an interception shim.

## Decision

structlog, configured in `guildhall.platform.logs`:

- One handler on the root logger, writing to stdout, where container runtimes
  collect it. structlog and standard `logging` records go through the same
  processors (`ProcessorFormatter`), so plug-ins and libraries keep using
  `logging.getLogger(__name__)` and the contract gains no dependency.
- Job fields live in context variables. `bind_job` binds them for the length
  of a block; asyncio gives each task its own copy of the context, so each
  concurrently running job sees only its own fields.
- `JobRunner.run` binds the fields once the envelope is parsed and logs one
  `job settled` line per message: INFO for done, duplicate and not ready,
  WARNING for retrying, ERROR for dead-lettered. A handler exception is also
  logged with its traceback.
- `GUILDHALL_LOG_FORMAT` (`json` or `console`) defaults to console on a
  terminal and JSON everywhere else; `GUILDHALL_LOG_LEVEL` defaults to INFO.

## Consequences

- One log format for the whole process, with no field plumbing through
  plug-in code.
- Settle reasons include exception messages from plug-ins, and they appear in
  logs and on dead-lettered messages. The M2 HTTP client must keep API keys
  out of exception messages (for example, Steam's key travels in the query
  string).
- `configure_logging` changes process-wide state; it is called once, at
  process start, and tests restore the previous state with a fixture.
- Two `conftest.py` files now exist, so mypy maps test modules by path
  (`explicit_package_bases`).
