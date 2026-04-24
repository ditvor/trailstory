.DEFAULT_GOAL := help
.PHONY: help dev install test lint format typecheck ci clean generate test-render

# ── Setup ──────────────────────────────────────────────────────────────────────

dev:                ## Install in editable mode with all dev dependencies
	pip install -e ".[dev]"

install:            ## Install runtime dependencies only (for production use)
	pip install .

# ── Quality ────────────────────────────────────────────────────────────────────

lint:               ## Check code with ruff
	ruff check .

format:             ## Auto-fix lint issues and format code
	ruff check --fix .
	ruff format .

typecheck:          ## Run mypy static type checking
	mypy trailstory/

test:               ## Run tests with coverage report
	pytest --cov=trailstory --cov-report=term-missing --cov-report=html

ci:                 ## Full CI check — lint, type check, tests (run before pushing)
	ruff check .
	ruff format --check .
	mypy trailstory/
	pytest --cov=trailstory --cov-fail-under=80

# ── Development helpers ────────────────────────────────────────────────────────

generate:           ## Run generator with sample fixtures (requires .env with API key)
	trailstory generate \
		--photos  tests/fixtures/sample_photos \
		--gpx     tests/fixtures/sample.gpx \
		--seed    "The fog cleared just as we reached the ridge." \
		--name    Mia \
		--age     5 \
		--out     output/dev

test-render:        ## Render the HTML template with fixture data (no API call)
	python -c "from tests.conftest import render_with_fixtures; render_with_fixtures()"

# ── Cleanup ────────────────────────────────────────────────────────────────────

clean:              ## Remove build artifacts, cache, and generated output
	rm -rf output/ htmlcov/ .coverage .mypy_cache .pytest_cache __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# ── Help ───────────────────────────────────────────────────────────────────────

help:               ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
