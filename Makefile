# Shortcuts for development tasks. Run `make` (or `make help`) to list them.

# The end-to-end test stack: its own compose project, so it never touches the
# development stack (docs/adr/0027).
E2E_TEST := docker compose --project-name guildhall-e2e-test \
	--file compose.yaml --file compose.e2e-test.yaml --profile app

.PHONY: help test-e2e test-e2e-up test-e2e-down kind-up kind-down kind-deploy

# The local Kubernetes cluster (docs/adr/0076). Its node image is built for
# this kind release, so the two are upgraded together.
KIND_CLUSTER := guildhall
KIND_VERSION := v0.33.0
# Every kubectl call names the kind cluster, so a target can never reach
# whichever cluster kubectl happens to point at.
KUBECTL := kubectl --context kind-$(KIND_CLUSTER)

help: ## List the targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "} {printf "  %-15s %s\n", $$1, $$2}'

test-e2e: ## Run the end-to-end tests on a throwaway stack, then remove it
	@trap '$(E2E_TEST) down --volumes --remove-orphans' EXIT; \
	$(E2E_TEST) up --detach --build --wait \
		&& uv run --locked pytest -m end_to_end \
		|| { $(E2E_TEST) logs migrate aws-setup worker api; exit 1; }

test-e2e-up: ## Start the end-to-end test stack and leave it running
	$(E2E_TEST) up --detach --build --wait

test-e2e-down: ## Remove the end-to-end test stack and its data
	$(E2E_TEST) down --volumes --remove-orphans

kind-up: ## Create the local Kubernetes cluster (does nothing if it exists)
	@kind version | grep -q '^kind $(KIND_VERSION) ' \
		|| { echo "kind $(KIND_VERSION) is required, found: $$(kind version)"; exit 1; }
	@if kind get clusters | grep -qx '$(KIND_CLUSTER)'; then \
		echo "Cluster $(KIND_CLUSTER) already exists"; \
	else \
		kind create cluster --config k8s/kind-cluster.yaml --wait 2m; \
	fi

kind-down: ## Delete the local Kubernetes cluster and everything in it
	kind delete cluster --name $(KIND_CLUSTER)

kind-deploy: ## Apply the Secret from .env and every manifest to the local cluster
	@test -f .env || { echo ".env is missing: copy .env.example and fill it in"; exit 1; }
	$(KUBECTL) apply -f k8s/namespace.yaml
	@# The Secret holds every line of .env (docs/adr/0079). kubectl writes it
	@# out without creating it, then server-side apply creates or updates it;
	@# --force-conflicts makes .env win over earlier hand-made changes.
	$(KUBECTL) create secret generic guildhall-secrets --namespace guildhall \
		--from-env-file=.env --dry-run=client --output yaml \
		| $(KUBECTL) apply --server-side --force-conflicts -f -
	$(KUBECTL) apply -f k8s/app -f k8s/local
