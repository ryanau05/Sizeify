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

See the root [Makefile](../../Makefile) and [CLAUDE.md](../../CLAUDE.md).

```bash
uv run pytest                                       # tests
uv run ruff check --fix && uv run ruff format       # lint + format
uv run mypy src                                     # type check
uv run alembic revision --autogenerate -m "..."     # new migration
uv run alembic upgrade head                         # apply migrations
```

### The two CI gates

Both run in `.github/workflows/backend.yml`; reproduce them locally with:

```bash
uv run ruff check                                   # incl. the route-layer boundary
uv run pytest --cov=api.domain --cov-fail-under=90  # pure-core coverage floor
```

**Route-layer boundary.** `src/api/routes/ruff.toml` bans `AsyncSession`,
`async_sessionmaker`, `Session`, and `deps.get_session` inside `src/api/routes/**`
only — the repository layer and `deps.py` still hold sessions freely. It is
ruff's `TID251`, scoped by ruff's per-directory config discovery, so a
violation shows up in the editor as well as in CI. This is CLAUDE.md's
"no raw SQLAlchemy sessions in route handlers" rule, which used to be
checkable only by hand.

A handler that genuinely needs a transaction boundary uses
`transaction(repo.session)` — the repository exposes its session on purpose,
and that stays allowed.

**Coverage floor.** Gated on `api.domain` alone. The pure cores are
deterministic functions with no I/O to excuse a gap, so a floor there means
something; gating routes and repositories would mostly punish thin handlers.
To see the wider picture without failing on it:

```bash
uv run pytest --cov=api --cov-report=term-missing
```

Coverage is configured with `concurrency = ["greenlet", "thread"]` in
`pyproject.toml`. Without it, SQLAlchemy's async layer switches greenlets
mid-call and coverage under-reports every line after an awaited query.
