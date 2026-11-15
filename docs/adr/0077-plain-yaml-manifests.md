# 0077. Kubernetes manifests are plain YAML

Status: Accepted (2026-11-15)

## Context

The bot runs on two clusters, kind locally ([ADR 0076](0076-kind-for-the-local-cluster.md))
and EKS on AWS, from mostly the same Kubernetes objects. They differ in a few
places: Postgres and moto run in the local cluster only, AWS adds an Ingress
and a ServiceAccount for Pod Identity, the settings point at different
services, and the image comes from a different place.

## Options considered

- **Plain YAML:** manifests applied with `kubectl apply -f`. Nothing to learn
  beyond Kubernetes itself; environment differences have to be expressed by
  which files are applied, or by duplicating the files that differ.
- **Kustomize:** a base of plain manifests plus an overlay per environment
  that adds objects, patches fields and swaps images; built into kubectl.
  Generated ConfigMaps and Secrets get a content hash in their name, so a
  settings change restarts the pods. Overlays and patches are one more layer
  to read.
- **Helm:** templated manifests filled from a values file per environment,
  with release history and rollback. Templates are not valid YAML until
  rendered, and its strengths serve software packaged for many users.

## Decision

Plain YAML. Guildhall is a small personal project with one app and two
environments whose differences are few and not expected to change much, so a
layer for expressing them is not worth its weight.

The differences are kept in separate files rather than inside shared ones:

- `k8s/app/` holds the objects both clusters run (api, worker, Redis, the
  migrate Job, Services); they read their settings from objects with the same
  names in every environment.
- `k8s/local/` holds what only the kind cluster runs (Postgres, moto, the
  `aws-setup` Job, the tunnel) and its settings; `k8s/aws/` will hold the
  EKS-only objects and settings.
- Guildhall's objects live in their own namespace, `guildhall`
  (`k8s/namespace.yaml`), apart from Kubernetes' own, so they can be listed
  or deleted together.
- A cluster gets `kubectl apply -f k8s/namespace.yaml -f k8s/app -f
  k8s/<environment>`, wrapped in make targets. The namespace comes first
  because `kubectl apply` creates objects in the order it reads them.

## Consequences

- Each manifest reads exactly as Kubernetes receives it.
- Pods read ConfigMaps and Secrets when they start, and nothing renames them
  when they change, so applying new settings needs a `kubectl rollout
  restart`; the make targets do it.
- Deleting a manifest does not delete its object from the cluster; it is
  removed by hand, or the local cluster is recreated.
- The image differs between clusters (a locally loaded tag on kind, ECR and a
  git SHA on EKS). How the AWS deployment sets it is decided with the AWS
  manifests; if more differences like that appear, Kustomize can be adopted
  then without rewriting the manifests, since its base is plain YAML.
