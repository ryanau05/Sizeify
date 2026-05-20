.PHONY: bootstrap dev lint test db-reset \
        lint-api lint-android lint-ios \
        test-api test-android test-ios

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
	docker compose -f $(COMPOSE_FILE) up -d
	cd apps/api && uv run alembic upgrade head
