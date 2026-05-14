.PHONY: bootstrap dev lint test db-reset

bootstrap:
	@echo "TODO: ./scripts/bootstrap.sh"

dev:
	@echo "TODO: start local stack + api in dev mode"

lint:
	@echo "TODO: ./scripts/lint-all.sh"

test:
	@echo "TODO: run api + ios + android test suites"

db-reset:
	@echo "TODO: docker compose down -v && docker compose up -d && alembic upgrade head"
