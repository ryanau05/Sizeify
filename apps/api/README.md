# Sizeify API

FastAPI backend for Sizeify. Python 3.12+ managed with [uv](https://docs.astral.sh/uv/).

## Bootstrap

```bash
uv sync
cp .env.example .env
```

## Run the dev server

```bash
uv run fastapi dev src/api/main.py
```

The server listens on http://127.0.0.1:8000 with hot reload. Smoke test:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

OpenAPI docs are at http://127.0.0.1:8000/docs.

## Tests, lint, migrations

These commands are stubs until later phases land — see the root [Makefile](../../Makefile) and [CLAUDE.md](../../CLAUDE.md).

```bash
uv run pytest                                       # tests
uv run ruff check --fix && uv run ruff format       # lint + format
uv run mypy src                                     # type check
uv run alembic revision --autogenerate -m "..."     # new migration
uv run alembic upgrade head                         # apply migrations
```
