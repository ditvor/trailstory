.DEFAULT_GOAL := help
.PHONY: help setup dev install install-hooks test lint format typecheck ci clean generate test-render golden-update eval eval-live eval-update-golden web web-dev

PYTHON ?= python3.12
VENV   := .venv
BIN    := $(VENV)/bin

# Prefer venv-installed tools when .venv exists; fall back to whatever is on
# PATH otherwise (for fresh checkouts and CI). This makes `make ci` work
# right after `make setup` without needing `source .venv/bin/activate` —
# the trap that bit during initial bring-up.
PY         := $(if $(wildcard $(BIN)/python),$(BIN)/python,python)
RUFF       := $(if $(wildcard $(BIN)/ruff),$(BIN)/ruff,ruff)
MYPY       := $(if $(wildcard $(BIN)/mypy),$(BIN)/mypy,mypy)
PYTEST     := $(if $(wildcard $(BIN)/pytest),$(BIN)/pytest,pytest)
TRAILSTORY := $(if $(wildcard $(BIN)/trailstory),$(BIN)/trailstory,trailstory)

# ── Setup ──────────────────────────────────────────────────────────────────────

setup:              ## One-shot: create .venv, install deps, install git hooks, install pre-commit
	@if [ ! -d $(VENV) ]; then \
		echo "→ creating $(VENV) with $(PYTHON)"; \
		$(PYTHON) -m venv $(VENV); \
	else \
		echo "→ $(VENV) already exists — reusing"; \
	fi
	@$(VENV)/bin/pip install --upgrade pip
	@$(VENV)/bin/pip install -e ".[dev]"
	@$(MAKE) --no-print-directory install-hooks
	@echo "→ installing pre-commit hooks"
	@$(VENV)/bin/pre-commit install --install-hooks
	@echo ""
	@echo "✓ setup complete. Activate with:  source $(VENV)/bin/activate"

dev:                ## Install in editable mode with all dev dependencies
	pip install -e ".[dev]"

install:            ## Install runtime dependencies only (for production use)
	pip install .

install-hooks:      ## Symlink scripts/hooks/* into .git/hooks/ (run once after clone)
	@mkdir -p .git/hooks
	@for hook in scripts/hooks/*; do \
		name=$$(basename $$hook); \
		ln -sf ../../$$hook .git/hooks/$$name; \
		echo "  linked $$name"; \
	done

# ── Quality ────────────────────────────────────────────────────────────────────

lint:               ## Check code with ruff
	$(RUFF) check .

format:             ## Auto-fix lint issues and format code
	$(RUFF) check --fix .
	$(RUFF) format .

typecheck:          ## Run mypy static type checking
	$(MYPY) trailstory/ web/

test:               ## Run tests with coverage report
	$(PYTEST) --cov=trailstory --cov=web --cov-report=term-missing --cov-report=html

ci:                 ## Full CI check — lint, type check, tests (run before pushing)
	$(RUFF) check .
	$(RUFF) format --check .
	$(MYPY) trailstory/ web/
	$(PYTEST) --cov=trailstory --cov=web --cov-fail-under=80

# ── Development helpers ────────────────────────────────────────────────────────

generate:           ## Run generator with sample fixtures (requires .env with API key)
	$(TRAILSTORY) generate \
		--photos  tests/fixtures/sample_photos \
		--gpx     tests/fixtures/sample.gpx \
		--seed    "The fog cleared just as we reached the ridge." \
		--out     output/dev

test-render:        ## Render fixtures under STYLE (default: all three styles, no API call)
	@if [ -n "$(STYLE)" ]; then \
		$(PY) -c "from tests.conftest import render_with_fixtures; from trailstory.models import Style; render_with_fixtures(style=Style('$(STYLE)'))"; \
	else \
		for s in editorial log encyclopedia; do \
			$(PY) -c "from tests.conftest import render_with_fixtures; from trailstory.models import Style; render_with_fixtures(style=Style('$$s'))"; \
		done; \
	fi

golden-update:      ## Regenerate tests/golden/test-render-<style>.html for every style
	@for s in editorial log encyclopedia; do \
		$(PY) -c "from tests.conftest import render_with_fixtures; from trailstory.models import Style; render_with_fixtures(style=Style('$$s'))"; \
		cp output/test/test-render-$$s.html tests/golden/test-render-$$s.html; \
		echo "✓ tests/golden/test-render-$$s.html refreshed"; \
	done

eval:               ## Run narrative rubric against every eval case (PAID — calls real Anthropic API)
	$(PY) -m tests.eval.run --all

eval-live:          ## Rubric + paid LLM-as-judge layer (PAID — writer + judge calls per case)
	$(PY) -m tests.eval.run --all --live-judge

eval-update-golden: ## Rewrite narrative AND judge goldens from a fresh paid run (PAID)
	$(PY) -m tests.eval.run --all --live-judge --update-golden

# ── Web builder ────────────────────────────────────────────────────────────────

web:                ## Run the FastAPI builder against the real Anthropic API (needs ANTHROPIC_API_KEY)
	$(PY) -m web --reload

web-dev:            ## Run the FastAPI builder with a fake LLM (free, deterministic narrative)
	$(PY) -m web --fake-llm --reload

# ── Cleanup ────────────────────────────────────────────────────────────────────

clean:              ## Remove build artifacts, cache, and generated output
	rm -rf output/ htmlcov/ .coverage .mypy_cache .pytest_cache __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# ── Help ───────────────────────────────────────────────────────────────────────

help:               ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
