# 0032. Archive keys, the bucket and shared AWS settings

Status: Accepted (2026-11-09)

## Context

ADR 0031 decides when archives are uploaded. This records where they go, how
a plug-in is kept inside its own objects, how they are stored, and how the
worker finds the bucket.

## Options considered

- **Key layout:** the plug-in builds the whole key, or the platform builds it
  from parts. Whole keys are flexible but let one plug-in name another's
  objects, which ADR 0030 rules out for tables.
- **Body format:** store the bytes as received, or re-serialise parsed JSON.
  Re-serialising can change number formatting and field order, so the
  archive would no longer be what the upstream sent.
- **Compression:** gzip would shrink JSON several times over, but about 2,000
  matches of up to a few MB each is a few GB, a few cents a month on AWS and
  free locally. It can be added later without changing the contract.
- **Settings:** a separate copy of region, endpoint and credentials for S3, or
  one shared set for every AWS client.
- **Creating the bucket:** by the worker at startup, or outside it as the
  queues are (ADR 0017).

## Decision

- Keys are `raw/<plug-in>/<kind>/<id>.json`; the platform adds `raw/<plug-in>/`.
  `kind` must match `[a-z][a-z0-9_]*`; `id` must start with a letter or digit,
  contain only letters, digits, `.`, `_` and `-`, and be at most 200
  characters. No slash means no way out of the prefix. A bad kind or id is a
  contract violation, dead-lettered, whether in an outcome or in
  `ctx.archive.get`. One outcome may not archive the same kind and id twice.
- Bodies are stored as received, as `application/json`, uncompressed.
- `AwsSettings` holds the region, endpoint and credentials, shared by
  `QueueSettings` and the new `ArchiveSettings` (`GUILDHALL_ARCHIVE_BUCKET`,
  default `guildhall-raw`). S3 errors are reported by error code only.
- The worker never creates the bucket. Locally, `guildhall.platform.dev_aws`
  (formerly `dev_queues`) creates the queues and the bucket, and the compose
  service that runs it is now `aws-setup`. On AWS, Terraform creates them.
  The worker checks the bucket at startup, so a wrong name fails at once.

## Consequences

- One plug-in cannot read or replace another's archived responses through the
  contract.
- The worker's AWS role needs `s3:PutObject` and `s3:GetObject` on the bucket,
  and `s3:ListBucket` too: without it, S3 answers a missing key with "access
  denied" instead of "no such key", and `get` would fail instead of returning
  None.
- Developers with a running app stack bring it up again with
  `--remove-orphans`, so the old `queues` container is removed.
