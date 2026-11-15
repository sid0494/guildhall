# 0078. Postgres, Redis and moto run in the cluster from our own manifests

Status: Accepted (2026-11-15)

## Context

The bot needs Postgres, Redis, and SQS and S3. In compose they are
containers ([ADR 0012](0012-local-infrastructure.md)). On EKS, Postgres is
RDS and SQS and S3 are the real services, but Redis stays in the cluster: it
only holds rate-limit buckets, which rebuild themselves. The kind cluster
([ADR 0076](0076-kind-for-the-local-cluster.md)) needs all of them, and the
spec's failure drills stop Redis and Postgres inside the cluster.

## Options considered

- **Our own manifests:** a StatefulSet with a volume for Postgres,
  Deployments for Redis and moto, and a Service for each, using the images
  compose already pins. Short and readable; we maintain them.
- **Third-party Helm charts (such as Bitnami's):** one command each, but
  built for production with hundreds of settings, they bring in Helm after
  [ADR 0077](0077-plain-yaml-manifests.md), and Bitnami's free images moved
  behind a paid offering in 2025.
- **A Postgres operator (such as CloudNativePG):** manages replicas,
  failover and backups through a custom resource. Real production practice,
  but heavy for a disposable database that is RDS on AWS.
- **Keep them in compose:** the kind nodes reach the compose containers over
  Docker networking. Fewest new files, but the cluster is not self-contained
  and the drills could not scale them down from Kubernetes.

## Decision

Our own manifests, with the same images as compose:

- **Postgres** (`k8s/local/`): a StatefulSet, so there is only ever one
  `postgres-0` and its 1 GiB volume is reattached whenever the pod is
  replaced; kind's local-path provisioner backs the volume with a folder on
  the node. A headless Service gives it the name `postgres`.
- **Redis** (`k8s/app/`, both clusters): a Deployment with persistence off,
  as in compose, and a Service.
- **moto** (`k8s/local/`): a Deployment and a Service.
- The Services are named `postgres`, `redis` and `moto`, as the compose
  services are, so the bot's settings are the same in both.
- Readiness probes run the same checks as compose's health checks, and each
  container requests the CPU and memory it needs and is limited in memory.
- Pods don't get Kubernetes' per-Service environment variables
  (`enableServiceLinks: false`): moto reads `MOTO_PORT`, which the `moto`
  Service would set to `tcp://<ip>:5000`.

## Consequences

- The kind cluster has its own database, separate from compose's; deleting
  the cluster deletes its data.
- moto keeps its state in memory, so a restarted moto pod has no queues or
  bucket until the `aws-setup` Job runs again.
- The images are pinned in compose and in the manifests. Dependabot updates
  compose; a unit test fails when the manifests name a different version of
  the same image, so a version bump touches both.
