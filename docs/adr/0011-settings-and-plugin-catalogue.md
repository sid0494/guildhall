# 0011. Environment settings with pydantic-settings and an explicit plug-in catalogue

Status: Accepted (2026-11-08)

## Context

The worker needs to know which plug-ins to run, and from M2 it needs secrets
(the Discord token, Steam and IsThereAnyDeal keys). The same container image
runs on a laptop, on kind and on EKS, and the repository is public, so
configuration cannot be committed. The spec requires that disabling a plug-in
is a configuration change, and ADR 0008 forbids the platform from importing
plug-ins.

## Options considered

Loading settings:

- **Reading `os.environ` by hand:** no dependency, but parsing, validation,
  `.env` loading and masking secrets in logs are all hand-written.
- **dynaconf or environs:** capable, but a different modelling style beside
  pydantic, with layered files and environments this project does not need.
- **pydantic-settings:** a typed settings class filled from environment
  variables and, locally, a `.env` file; validation errors at startup;
  `SecretStr` masks secrets when printed.

Mapping configured names to plug-in code:

- **Python entry points:** the standard way to discover plug-ins from other
  installed packages, which this project does not have; changes need a
  reinstall to take effect.
- **Dotted import paths in configuration:** flexible, but configuration can
  import arbitrary code and typos surface as import errors.
- **An explicit catalogue in code:** one mapping of name to class; visible,
  type-checked, and an unknown name can list the available ones.

## Decision

- Configuration follows the twelve-factor rule: it lives in environment
  variables, all prefixed `GUILDHALL_`, loaded by a pydantic-settings
  `Settings` class. Locally a git-ignored `.env` supplies them; `.env.example`
  documents every variable with placeholder values. Real environment
  variables override `.env`.
- `GUILDHALL_PLUGINS` is a required, comma-separated list of enabled plug-in
  names; startup fails if it is missing or empty.
- `guildhall.plugins.CATALOGUE` maps each available plug-in's name to its
  class. `guildhall.platform.registry.build_registry` receives the catalogue
  and the enabled names, instantiates each plug-in, runs
  `check_declarations`, and raises `RegistryError` listing every problem
  (unknown or repeated names, a name mismatch, failed checks).
- `guildhall.bootstrap` is the composition root: the only module that imports
  both the platform and the plug-ins, used by entry points such as the worker.

## Consequences

- A typo in configuration stops startup with a message naming the available
  plug-ins, instead of failing on the first scheduled run.
- Adding a plug-in is one catalogue entry; a guard test fails if a plug-in
  class is missing from the catalogue.
- Secrets added in M2 will be `SecretStr` fields with placeholders in
  `.env.example`.
- Plug-ins are created without arguments for now; how they receive their own
  configuration (such as API keys) is designed in M2.
- Tests that construct `Settings` must isolate themselves from the developer's
  environment and `.env` file.
