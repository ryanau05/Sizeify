# Phase 1 — Tickets

Phase 1 of `PROJECT_PLAN.md` ("Backend core: schema, auth, closet CRUD," weeks 2–3) decomposed into atomic tickets. Each ticket should fit in 0.5–2 days of focused work and produce a reviewable PR.

**Phase 1 exit criterion (re-stated from the plan):** an integration test seeds a closet with five garments, calls the matching engine against a fixture `brand_product`, and asserts the recommendation contains size + confidence + fit notes + reference garments matching a hand-computed expectation.

All tickets target `apps/api`. Refs are to `Sizeify_PRD_v1.docx` and `CLAUDE.md`. Paths follow `FILE_STRUCTURE.md` (the architecture source of truth): auth helpers live in `auth/`, stretch logic in `domain/stretch.py`, the current-user dependency in `deps.py`.

---

## Phase 1 prerequisites (foundation)

Small foundation tasks the tickets below assume but the Phase 0 skeleton doesn't yet provide. Land them first; a single PR is fine. Paths follow `FILE_STRUCTURE.md`.

### TKT-P1-00a — Declarative `Base` + Alembic autogenerate wiring

**Scope:** Establish the SQLAlchemy declarative base and point Alembic at it so `--autogenerate` emits real diffs.

**Deliverables**

- `Base(DeclarativeBase)` in `apps/api/src/api/models/__init__.py`, importing every entity module so they register on one shared metadata.
- `apps/api/alembic/env.py` sets `target_metadata = Base.metadata` (replacing `None`).

**Acceptance**

- With one model defined, `uv run alembic revision --autogenerate -m "probe"` emits a non-empty migration (discard the probe); `uv run alembic upgrade head` clean.

**Refs:** FILE_STRUCTURE.md (`models/`); CLAUDE.md backend rules.

### TKT-P1-00b — Async engine, session, and `get_session` dependency

**Scope:** The async DB plumbing every repository and route depends on. No `AsyncSession` in route handlers.

**Deliverables**

- Async engine + `async_sessionmaker` + transaction helper in `apps/api/src/api/repositories/base.py`.
- `get_session` FastAPI dependency in `apps/api/src/api/deps.py` yielding an `AsyncSession`, rolling back on error.

**Acceptance**

- A throwaway route using `Depends(get_session)` round-trips `SELECT 1` against the compose Postgres; `uv run mypy src` clean.

**Refs:** FILE_STRUCTURE.md (`deps.py`, `repositories/base.py`); CLAUDE.md backend rules.

### TKT-P1-00c — Expand `Settings` for Phase 1

**Scope:** Surface the env vars already declared in `.env.example` through `config.py`.

**Deliverables**

- `Settings` fields: `redis_url`, `anthropic_api_key`, `jwt_secret`, `access_token_ttl`, `refresh_token_ttl` (local-safe defaults; secrets default to empty). Existing `database_url` and resolution order unchanged.

**Acceptance**

- `uv run python -c "from api.config import get_settings; get_settings()"` loads with no error; `uv run mypy src` clean.

**Refs:** `apps/api/.env.example`; FILE_STRUCTURE.md (`config.py`).

### TKT-P1-00d — Phase 1 backend dependencies

**Scope:** Add the libraries the auth and coverage tickets need, pinned ADR-baseline style.

**Deliverables**

- Runtime: `argon2-cffi` (TKT-P1-05), `pyjwt` (TKT-P1-06). Dev: `pytest-cov` (TKT-P1-20). (`email-validator` already ships via `fastapi[standard]`.)
- `uv lock` committed.

**Acceptance**

- `uv sync --locked` clean; `uv run python -c "import argon2, jwt"` succeeds.

**Refs:** ADR 0001 (dependency pinning); TKT-P1-05/06/20.

### TKT-P1-00e — DB test fixtures

**Scope:** Transactional test-DB harness so Phase 1 integration tests run isolated.

**Deliverables**

- `tests/conftest.py` fixtures: a per-test transactional `AsyncSession` (rolled back after each test) and a DB-backed `AsyncClient` that overrides `get_session`. Document the test-DB source (compose Postgres vs testcontainers) inline. Optional `factory_boy` scaffold.

**Acceptance**

- A row created in one test is invisible in another (isolation proven); `uv run pytest` green.

**Refs:** FILE_STRUCTURE.md (`tests/conftest.py`); TKT-P1-03.

---

## TKT-P1-01 — Alembic migration: six core entities + §8.2 indexes

**Scope:** Single initial migration creating `user`, `garment_category`, `owned_garment`, `fit_signal`, `brand_product`, `recommendation` per PRD §8, with `recommendation.prompt_version` and `fit_signal.source` / `fit_signal.raw_feedback_text` non-nullable.

**Deliverables**

- SQLAlchemy models under `apps/api/src/api/models/` (one file per entity).
- Single Alembic revision `0001_core_schema.py` covering all six tables plus PRD §8.2 indexes.
- `owned_garment.measurements` and `brand_product.size_chart` as JSONB; measurements stored in cm.
- `fit_signal.verdict` enum: `too_tight | slightly_tight | preferred | slightly_loose | too_loose | slightly_short | too_short`.
- `fit_signal.source` enum: `nlp_extracted | user_edited | user_added`.

**Acceptance**

- `uv run alembic upgrade head` then `downgrade base` cycles clean against the docker-compose Postgres.
- `uv run mypy src` clean on the new models.

**Refs:** PRD §8, §8.2, §6.1; CLAUDE.md domain conventions.

---

## TKT-P1-02 — Seed data: men's button-down shirt category

**Scope:** Seed script populating one `garment_category` row with the v1 measurement schema (chest, body length, shoulder, sleeve, neck, cuff) and dimension weights.

**Deliverables**

- `apps/api/src/api/seeds/garment_categories.py` invoked via `uv run python -m api.seeds.garment_categories`.
- `apps/api/src/api/domain/dimension_weights.py` exporting the canonical per-dimension weights used by the matching engine.
- Idempotent: re-running does not duplicate rows.

**Acceptance**

- After seed, one row exists with `slug='mens_button_down_shirt'`.
- Unit test asserts schema and weights match PRD §6.1 / §6.4.

**Refs:** PRD §6.1, §8.

---

## TKT-P1-03 — Repository layer scaffolding

**Scope:** Generic async repository base + one repository per entity. No raw `AsyncSession` may appear in route handlers (CLAUDE.md backend rule).

**Deliverables**

- `apps/api/src/api/repositories/base.py` with generic `get / list / create / update / delete` over `asyncpg`/SQLAlchemy async session.
- Per-entity repositories: `users.py`, `garment_categories.py`, `owned_garments.py`, `fit_signals.py`, `brand_products.py`, `recommendations.py`.
- FastAPI dependency `get_session` + per-repo dependency providers.

**Acceptance**

- Unit tests per repo covering happy-path CRUD against an ephemeral Postgres (testcontainers or docker-compose-backed fixture).
- `grep -r "AsyncSession" apps/api/src/api/routes` returns nothing.

**Refs:** CLAUDE.md backend rules.

---

## TKT-P1-04 — Pydantic schemas for all request/response bodies

**Scope:** Pydantic v2 models for every Phase 1 endpoint's request and response. Route handlers must never return raw dicts.

**Deliverables**

- `apps/api/src/api/schemas/` with submodules `auth.py`, `closet.py`, `fit_profile.py`, `me.py`.
- `MeasurementValue` model = `{value: float, unit: Literal['cm'], source: Literal['manual_tape','cv_assisted','imported']}` (forward-compat with PRD §A.9).
- Verdict / source / stretch enums imported from a shared `enums.py`.

**Acceptance**

- `uv run mypy src` clean.
- Round-trip test: serialize → JSON → deserialize for one example of every schema.

**Refs:** CLAUDE.md backend rules; PRD §A.9 (CV-assist forward compat).

---

## TKT-P1-05 — Argon2 password hashing

**Scope:** Pure-utility module wrapping `argon2-cffi` with parameters calibrated for ~250 ms verify on prod-equivalent hardware.

**Deliverables**

- `apps/api/src/api/auth/password.py` exposing `hash(password) -> str` and `verify(password, hash) -> bool`.
- Constants for memory/iterations/parallelism with a comment recording the calibration target.

**Acceptance**

- Unit tests: hashes are unique per call; verify succeeds for correct, fails for incorrect.
- Benchmark test (marked `slow`) asserts verify completes in < 500 ms.

**Refs:** PRD §11; CLAUDE.md backend rules.

---

## TKT-P1-06 — JWT issuance, validation, and rotation

**Scope:** Short-lived access token + long-lived refresh token, with refresh rotation (single-use refresh tokens).

**Deliverables**

- `apps/api/src/api/auth/jwt.py`: `issue_pair(user_id) -> (access, refresh)`, `decode(token) -> Claims`, `rotate(refresh) -> (access, refresh)`.
- `refresh_token` table (migration `0002_refresh_tokens.py`) storing hashed token, `user_id`, `issued_at`, `revoked_at`.
- Symmetric HS256 with secret loaded from env; key-rotation hook documented.

**Acceptance**

- Reusing a rotated refresh token returns 401 and revokes all of that user's refresh tokens.
- Unit tests cover: expired access, expired refresh, tampered token, reused refresh.

**Refs:** PRD §11; CLAUDE.md backend rules.

---

## TKT-P1-07 — Auth endpoints with consent flag

**Scope:** `POST /auth/signup`, `POST /auth/login`, `POST /auth/refresh`. Signup accepts and stores a `privacy_consent_accepted_at` timestamp (GDPR/CCPA day-one requirement).

**Deliverables**

- Route module `apps/api/src/api/routes/auth.py`.
- Request schemas validate email RFC compliance and password complexity (>= 10 chars, mixed classes).
- Rate-limit middleware applied to `/auth/*` (in-memory bucket is fine in Phase 1; Redis-backed deferred to Phase 8).

**Acceptance**

- Integration tests: signup-then-login round trip; refresh issues new pair; signup without consent flag → 422.
- `pytest -k auth` green.

**Refs:** PRD §11; PROJECT_PLAN.md Phase 1.

---

## TKT-P1-08 — Auth dependency / current-user resolver

**Scope:** FastAPI dependency that decodes the `Authorization: Bearer` header, loads the user, and 401s on any failure. Used by every closet/me endpoint.

**Deliverables**

- `apps/api/src/api/deps.py` exporting `CurrentUser = Annotated[User, Depends(...)]` (current-user resolver lives alongside `get_session` per FILE_STRUCTURE.md).
- Helpful 401 messages distinguishing missing / malformed / expired tokens (no info leakage on user existence).

**Acceptance**

- Integration test: protected endpoint returns 401 without token, 200 with valid token.

---

## TKT-P1-09 — Closet CRUD endpoints

**Scope:** `GET /closet/garments`, `POST /closet/garments`, `PATCH /closet/garments/{id}`, `DELETE /closet/garments/{id}`. All scoped to the current user.

**Deliverables**

- Route module `apps/api/src/api/routes/closet.py`.
- Validation: measurements present for all required dimensions of the garment's category; values within PRD §5.2 ranges; rejects out-of-scope categories (v1 = button-down only).
- Soft-delete on DELETE (sets `deleted_at`) so historic recommendations remain interpretable.

**Acceptance**

- Integration tests cover create → list → patch → delete; ownership isolation (user A cannot read user B's garments).
- Out-of-range measurement → 422 with a per-field error.

**Refs:** PRD §5.2, §8; CLAUDE.md v1 scope rules.

---

## TKT-P1-10 — Manual fit-signal entry endpoint

**Scope:** `POST /closet/garments/{id}/fit-signals` for the v1 manual entry path. Creates rows with `source='user_added'`.

**Deliverables**

- Route handler + Pydantic schema accepting `dimension`, `verdict`, optional `magnitude_cm`, optional `use_case`, optional `raw_feedback_text`.
- `raw_feedback_text` defaults to a synthesized string (e.g. "User-added: chest slightly tight") so the column is never null — required by CLAUDE.md.

**Acceptance**

- Integration test: signal created, retrievable via fit-profile endpoint, `source='user_added'`.
- Schema validation rejects unknown verdicts or dimensions not in the garment's category schema.

**Refs:** PRD §6.1; CLAUDE.md domain conventions.

---

## TKT-P1-11 — Stretch coefficients module

**Scope:** Encode the v1 hand-tuned stretch coefficients per stretch level.

**Deliverables**

- `apps/api/src/api/domain/stretch.py` exporting `effective_measurement(measurement_cm, stretch_level) -> float` and a constants table.
- Module-level comment forbidding any "learning loop" change without scope approval (CLAUDE.md gotchas).

**Acceptance**

- Unit tests: each of `none | slight | moderate | high` returns the documented multiplier; unknown level raises.

**Refs:** PRD §6.3; CLAUDE.md gotchas.

---

## TKT-P1-12 — Fit-profile construction (pure function)

**Scope:** `build_fit_profile(closet_snapshot) -> FitProfile` per PRD §6.2. Bayesian update with weak prior, owned-garment evidence, use-case conditioning. No DB or HTTP access.

**Deliverables**

- `apps/api/src/api/domain/fit_profile.py` with `build_fit_profile` and supporting dataclasses.
- Unit-test fixtures: empty closet, single love-rated garment, contradictory signals, use-case-specific signals (gym vs office), maturity calculation.

**Acceptance**

- ≥ 95% line coverage on the module.
- Each fixture asserts the full profile shape (per-dimension preferred value + spread + maturity).

**Refs:** PRD §6.2.

---

## TKT-P1-13 — `GET /closet/fit-profile` endpoint

**Scope:** Endpoint that builds the current user's fit profile from their closet snapshot and returns it.

**Deliverables**

- Route handler + response schema mirroring the `FitProfile` dataclass.
- Empty closet → 200 with `maturity='cold_start'` and a hint message.

**Acceptance**

- Integration test: seed two garments + signals → response matches `build_fit_profile` output exactly.

**Refs:** PRD §6.2; PROJECT_PLAN.md Phase 1.

---

## TKT-P1-14 — Matching engine (pure function)

**Scope:** `match(fit_profile, brand_product) -> RankedSizes` per PRD §6.4. Applies stretch adjustment, computes weighted distance per size, ranks.

**Deliverables**

- `apps/api/src/api/domain/matching.py` with `match` and `RankedSize` dataclass (`size, distance, per_dimension_deltas`).
- Uses `dimension_weights` from TKT-P1-02 and `stretch` (`domain/stretch.py`) from TKT-P1-11.

**Acceptance**

- Unit tests covering: in-range fit picks closest size, weighted distance ranking respects per-dimension weights, stretch-adjusted measurement changes ranking, missing dimension in product chart raises a typed error.

**Refs:** PRD §6.4, §6.3.

---

## TKT-P1-15 — Confidence + two-candidate selection

**Scope:** `recommend(fit_profile, brand_product) -> Recommendation` wraps `match` and applies the confidence rule. Confidence < 60% → two candidates returned with trade-offs (PRD §5.4).

**Deliverables**

- `apps/api/src/api/domain/recommendation.py` exposing `recommend`.
- Confidence calculation driven by gap-to-second-best plus profile maturity.
- Output contains all four PRD §5.4 mandatory components: size, confidence, fit notes (per-dimension delta narrative), reference garments (top owned garments driving the call).

**Acceptance**

- Unit tests: confidence 85% returns one candidate; confidence 45% returns two with `tradeoff` strings; cold-start profile clamps confidence ≤ 50%.

**Refs:** PRD §5.4, §6.4.

---

## TKT-P1-16 — Recommendation persistence

**Scope:** Persist `recommendation` rows for every `recommend` call, including `prompt_version` (even in Phase 1 the column is required — store a `'manual-v0'` sentinel).

**Deliverables**

- Repository method `recommendations.create_from(recommend_output, user_id, brand_product_id)`.
- `prompt_version` constant in `apps/api/src/api/llm/versions.py` (placeholder until Phase 3 lands real versions).

**Acceptance**

- Integration test: call recommendation flow → row exists with all four components and `prompt_version='manual-v0'`.

**Refs:** CLAUDE.md domain conventions (prompt_version traceability).

---

## TKT-P1-17 — `GET /me/export` (GDPR/CCPA data export)

**Scope:** Full JSON dump of the user's data: profile, closet, signals, recommendations.

**Deliverables**

- Route `apps/api/src/api/routes/me.py::export`.
- Response is a single JSON object versioned with an `export_schema_version` field.
- Excludes anything not owned by the user (no global tables like `brand_product` chart data).

**Acceptance**

- Integration test: seeded user → export contains every owned row; another user's data not present.

**Refs:** PRD §11.

---

## TKT-P1-18 — `DELETE /me` (cascade delete)

**Scope:** Hard-deletes the user and all owned rows. Idempotent: re-calling returns 401 (token revoked).

**Deliverables**

- Route handler + repository method orchestrating cascade.
- All refresh tokens for the user revoked atomically.
- Foreign keys on owned tables use `ON DELETE CASCADE` (verify in TKT-P1-01 migration; add follow-up migration if missing).

**Acceptance**

- Integration test: delete → subsequent calls with old tokens return 401; row counts for that user are zero across all tables.

**Refs:** PRD §11.

---

## TKT-P1-19 — Phase 1 exit-criterion integration test

**Scope:** The exit criterion from the project plan. End-to-end test that seeds a five-garment closet, runs the matching engine against a fixture `brand_product`, asserts the full recommendation shape.

**Deliverables**

- `apps/api/tests/integration/test_phase1_exit.py`.
- Hand-computed expected recommendation documented inline as a comment block so future drift is auditable.
- Five-garment fixture covers: two preferred-fit, one slightly-tight chest, one slightly-loose body, one with use-case='gym'.

**Acceptance**

- Test green; output matches the hand-computed expectation including all four §5.4 components.

**Refs:** PROJECT_PLAN.md Phase 1 exit criterion; PRD §5.4.

---

## TKT-P1-20 — CI: enforce repo-layer boundary + Phase 1 coverage gate

**Scope:** Codify the CLAUDE.md "no raw AsyncSession in routes" rule and require ≥ 90% coverage on `api/domain/*` (pure cores).

**Deliverables**

- `ruff` custom rule or a `pytest` static check that fails CI when `AsyncSession` is imported in `api/routes/**`.
- `pytest --cov=api.domain --cov-fail-under=90` wired into the backend GitHub Actions workflow.

**Acceptance**

- Intentional violation PR fails CI; removing it returns to green.

**Refs:** CLAUDE.md backend + cross-cutting concerns.

---

## Suggested execution order

Roughly two parallel tracks. Track A is the data/auth spine; Track B is the domain core that can be developed against in-memory fixtures while the spine lands.

- **Prerequisites (land first, single PR):** 00a → 00b → 00c → 00d → 00e
- **Track A (spine):** 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 → 17 → 18
- **Track B (domain):** 11 → 12 → 14 → 15 → 16 (can start once 02 + 04 are merged)
- **Integration:** 13 (needs 12 + 08), then 19 (needs everything), then 20.
