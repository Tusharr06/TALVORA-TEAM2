.PHONY: check-backend test-unit test-integration lint-backend typecheck-backend openapi dev db-upgrade

check-backend: lint-backend typecheck-backend test-unit test-integration
	@echo "All backend checks passed successfully."

lint-backend:
	cd backend && uv run ruff check src/ tests/
	cd backend && uv run ruff format --check src/ tests/

typecheck-backend:
	cd backend && uv run mypy src/ tests/

test-unit:
	cd backend && uv run pytest -m "not integration and not live" -v

test-integration:
	cd backend && uv run pytest -m "integration" -v

openapi:
	python scripts/export_openapi.py

dev:
	cd backend && uv run uvicorn talvora.main:app --reload --port 8000

db-upgrade:
	@echo "No migrations in Phase P01. Migration engine starts in P02."
