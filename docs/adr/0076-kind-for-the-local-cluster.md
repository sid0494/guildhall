# 0076. kind runs the local Kubernetes cluster

Status: Accepted (2026-11-15)

## Context

M3 runs Guildhall on Kubernetes: EKS on AWS, and a cluster on the
development machine first. An EKS session bills by the hour, so the manifests,
probes and limits should be proven locally for free, leaving only the
AWS-specific parts (load balancer, IAM, RDS) to paid sessions. The failure
drills and the load check in the spec also run on the local cluster.

No local cluster matches EKS exactly: EKS uses the VPC CNI for pod networking,
EBS for volumes and the AWS Load Balancer Controller for Ingress. What matters
is how little a local tool adds beyond upstream Kubernetes, because anything
it adds can make a manifest work locally and fail on EKS.

## Options considered

- **kind (Kubernetes in Docker):** each node is a Docker container that
  bootstraps upstream Kubernetes with kubeadm, adding only a minimal pod
  network, CoreDNS and a local-path volume provisioner. The cluster is one
  YAML file, multi-node is a line per node, and it is the tool Kubernetes
  tests itself with. It has no load balancer or ingress, and locally built
  images must be loaded into the nodes.
- **k3d (k3s in Docker):** the fastest and lightest. k3s is a trimmed
  distribution that keeps state in SQLite by default and bundles Traefik,
  ServiceLB and metrics-server, so a Service or Ingress can work locally only
  because of them; turning them off leaves roughly kind. Has a built-in
  registry.
- **minikube:** also kubeadm-based, run as a VM or container, with add-ons
  for ingress, dashboards and registries. Configured mostly through commands
  rather than a committed file, multi-node is less used, and it is the
  heaviest of the three.

## Decision

kind. It is the closest to the upstream Kubernetes that EKS runs, and its
gaps do not matter here: Discord reaches the api through the tunnel locally
and through the ALB on AWS, so no local load balancer is needed.

- The cluster is defined in `k8s/kind-cluster.yaml`: one control-plane node
  and one worker node. The control plane is tainted against workloads, so the
  bot's pods run on the worker, as they run on EKS's nodes with the control
  plane out of sight; a second worker can be added for drills.
- The node image is pinned by version and digest to the Kubernetes minor
  version planned for EKS (1.37), so both clusters serve the same API. kind
  builds node images for its own release, so the kind version is pinned
  with it (v0.33.0).
- `make kind-up` and `make kind-down` create and delete it. It is free and
  can be deleted between sessions; nothing in it is meant to persist.

## Consequences

- An image built locally is not visible to the nodes, which run their own
  containerd: it has to be loaded into the cluster or served from a registry.
  That is decided separately.
- No Service of type LoadBalancer works locally; the manifests use
  ClusterIP Services, and the AWS overlay adds the Ingress.
- Upgrading means bumping kind, the node image and kubectl together, and
  keeping the minor version in step with EKS.
