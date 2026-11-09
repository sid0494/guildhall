# Shortcuts for development tasks. Run `make` (or `make help`) to list them.

# The end-to-end test stack: its own compose project, so it never touches the
# development stack (docs/adr/0027).
E2E_TEST := docker compose --project-name guildhall-e2e-test \
	--file compose.yaml --file compose.e2e-test.yaml --profile app

.PHONY: help test-e2e test-e2e-up test-e2e-down

help: ## List the targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "} {printf "  %-15s %s\n", $$1, $$2}'

test-e2e: ## Run the end-to-end tests on a throwaway stack, then remove it
	@trap '$(E2E_TEST) down --volumes --remove-orphans' EXIT; \
	$(E2E_TEST) up --detach --build --wait \
		&& uv run --locked pytest -m end_to_end \
		|| { $(E2E_TEST) logs migrate queues worker; exit 1; }

test-e2e-up: ## Start the end-to-end test stack and leave it running
	$(E2E_TEST) up --detach --build --wait

test-e2e-down: ## Remove the end-to-end test stack and its data
	$(E2E_TEST) down --volumes --remove-orphans
