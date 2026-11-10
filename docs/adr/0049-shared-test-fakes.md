# 0049. Shared context fakes, and contract examples that bring their own context

Status: Accepted (2026-11-10)

## Context

`NoDatabase`, `NoArchive` and `NoHttp`, fakes that fail a test if used, were
copied into three test files, and the contract suite ran every example with
them. Gank Report's handlers do nothing but I/O, so their tests need fakes
that answer (an OpenDota serving recorded fixtures, an archive, preset rows),
and the contract suite needs a context per example.

## Options considered

- **Keep copying:** more and larger copies, drifting apart.
- **One test-support module outside `src/`:** importable by name, as the
  fixture recorder is; test code stays out of the package and the image.
- **`guildhall.contract.testing` in `src/`:** how frameworks ship fakes to
  external plug-in authors; ours live in this repository, so it would ship
  test code for no one.

## Decision

`tests/support/fakes.py`, on pytest's `pythonpath` and mypy's `mypy_path`,
holds the three failing fakes, `no_io_context()`, `FakeHttp` (preset responses
by method and URL; records each call; fails on any other), `DictArchive`,
`RowsDatabase` (the same preset rows for every read) and `response()`. They
implement the contract's protocols structurally, so mypy checks them wherever
they are passed to `Context`. Contract-suite examples are
`Example(payload, context, attempt)`, where `context` is a factory called per
run, defaulting to `no_io_context`.

## Consequences

- `RowsDatabase` cannot tell whether a query is right; plug-in SQL is tested
  against Postgres in integration tests.
- The fakes move into `src/` if plug-ins ever become separate packages.
