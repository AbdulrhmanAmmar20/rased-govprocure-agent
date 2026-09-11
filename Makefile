# Common tasks. Every target works with only requirements.txt installed.

PYTHON ?= python
export PYTHONPATH := $(CURDIR)
export PYTHONIOENCODING := utf-8

.DEFAULT_GOAL := help
.PHONY: help install install-stack test test-acceptance lint format run demo verify-audit seed docker-build compose-up compose-down clean

help: ## Show the available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sed -e 's/:.*## /	/'

install: ## Install the runtime floor
	$(PYTHON) -m pip install -r requirements.txt

install-stack: ## Install the full SRS section-5 stack (LangGraph, Presidio, Chroma)
	$(PYTHON) -m pip install -r requirements.txt -r requirements-stack.txt

test: ## Run the whole suite
	$(PYTHON) -m unittest discover -s tests -v

test-acceptance: ## Run only the SRS section 6 acceptance scenario
	$(PYTHON) -m unittest tests.test_acceptance -v

lint: ## Lint with ruff
	ruff check app scripts tests

format: ## Format with ruff
	ruff format app scripts tests

run: ## Start the API on :8000
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

demo: ## Walk the acceptance scenario in the terminal
	$(PYTHON) -m app.cli demo --quiet

verify-audit: ## Verify the audit hash chain
	$(PYTHON) -m app.cli verify-audit

seed: ## Validate and index the regulation corpus
	$(PYTHON) -m scripts.seed_regulations

docker-build: ## Build the container image
	docker build -t rased/govprocure-agent:1.0.0 .

compose-up: ## Start the local sovereign stack
	docker compose up -d --build

compose-down: ## Stop the stack (audit volume is preserved)
	docker compose down

clean: ## Remove caches and local runtime artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
