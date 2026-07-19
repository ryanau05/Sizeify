.PHONY: bootstrap dev lint test db-reset \
        lint-api lint-android lint-ios \
        test-api test-android test-ios \
        demo demo-api demo-web demo-seed

COMPOSE_FILE := infra/docker-compose.yml

# Idempotent dev-environment setup (prereq checks, deps, infra, migrations, hooks).
bootstrap:
	./scripts/bootstrap.sh

# Run the backend dev server.
dev:
	cd apps/api && uv run fastapi dev src/api/main.py

# ---------------------------------------------------------------------------
# Lint (delegates to each app)
# ---------------------------------------------------------------------------
lint: lint-api lint-android lint-ios

lint-api:
	cd apps/api && uv run ruff check && uv run ruff format --check && uv run mypy src

lint-android:
	cd apps/android && ./gradlew ktlintCheck detekt

lint-ios:
	@if [ "$$(uname -s)" = "Darwin" ]; then \
		cd apps/ios && swift format lint -r Sizeify/ && swiftlint; \
	else \
		echo "iOS lint skipped (not macOS)"; \
	fi

# ---------------------------------------------------------------------------
# Test (delegates to each app)
# ---------------------------------------------------------------------------
test: test-api test-android test-ios

test-api:
	cd apps/api && uv run pytest

test-android:
	cd apps/android && ./gradlew test

test-ios:
	@echo "iOS: no test target yet — skipping (see apps/ios/README.md)"

# ---------------------------------------------------------------------------
# Destructive: wipe and recreate the local database.
# ---------------------------------------------------------------------------
db-reset:
	docker compose -f $(COMPOSE_FILE) down -v
	docker compose -f $(COMPOSE_FILE) up -d --wait
	cd apps/api && uv run alembic upgrade head

# ---------------------------------------------------------------------------
# DEMO (capstone, throwaway) — additive targets, isolated from production.
# Everything below depends only on apps/web + apps/api/src/api/demo/* and is
# safe to delete with `rm -rf apps/web apps/api/src/api/demo` after the demo.
# ---------------------------------------------------------------------------
# One-command: infra up, migrate, seed, print next steps.
demo:
	./scripts/demo.sh

# Backend demo app (separate ASGI entrypoint; production stays api.main:app).
demo-api:
	cd apps/api && DEMO_MODE=1 uv run uvicorn api.demo.app:app --reload --port 8000

# Web demo client (Vite dev server on :5173).
demo-web:
	cd apps/web && npm run dev

# (Re)seed the demo user, closet, and brand products.
demo-seed:
	cd apps/api && DEMO_MODE=1 uv run python -m api.demo.seed_demo
