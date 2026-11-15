# 0079. The local cluster's Secret is made from .env

Status: Accepted (2026-11-15)

## Context

The bot's secrets (the Postgres password, the Discord bot token, the ngrok
token) and personal identifiers (the Discord application, server and channel
IDs) live in a git-ignored `.env` ([ADR 0011](0011-settings-and-plugin-catalogue.md)),
which compose reads. The kind cluster needs the same values without any of
them being committed to this public repository. Some values in `.env` are
written for running on the host (moto at `127.0.0.1`), so the cluster also
needs a way to override them.

## Options considered

- **All of `.env` as one Secret, made by a make target:** `kubectl create
  secret --from-env-file=.env`, applied so it creates or updates. Nothing to
  keep in sync and every value is treated as sensitive; harmless settings end
  up in the Secret too, and kubectl keeps quotes as part of a value.
- **Chosen keys only:** secret keys into a Secret, the rest into a ConfigMap.
  A cleaner split, but the key list lives in the Makefile, and a new setting
  missing from it silently never reaches the cluster.
- **Encrypted secrets in git (SOPS, Sealed Secrets):** git as the single
  source, as in GitOps teams; a new tool and key to look after, and personal
  IDs published, encrypted, for good.
- **An external store (AWS Secrets Manager and the External Secrets
  Operator):** production-grade, but AWS-only and billed per secret; it does
  not help the kind cluster.

## Decision

All of `.env` becomes the Secret `guildhall-secrets` in the `guildhall`
namespace, made by `make kind-deploy`:

- `kubectl create secret generic --from-env-file=.env --dry-run=client`
  writes the object out without creating it, and `kubectl apply
  --server-side` creates or updates it. Server-side apply keeps no copy of
  the applied object in an annotation, as client-side apply would, so the
  values are stored once, and a key removed from `.env` is removed from the
  Secret.
- Pods read single keys with `secretKeyRef`, or every key with `envFrom`.
  Settings that differ in the cluster, such as service addresses, will come
  from a committed ConfigMap listed after the Secret, since the last source
  to set a key wins.
- `.env` must not quote values: compose and pydantic strip quotes, kubectl
  does not.

## Consequences

- The cluster runs with exactly the values compose uses, plus overrides that
  are visible in the repository.
- Pods read the Secret when they start, so a change to `.env` reaches them
  only after a restart. Postgres reads its password only when it first
  creates its data, so changing it means recreating the volume.
- The values sit base64-encoded, not encrypted, in the kind cluster's etcd,
  readable by anyone with its kubeconfig; acceptable for a cluster on this
  machine.
- On AWS the moto settings must be absent rather than overridden, so the EKS
  Secret will be made from its own git-ignored file; decided with the AWS
  manifests.
