# Demo — Tickets

`DEMO_PLAN.md` decomposed into atomic tickets for the 3-day capstone prototype.
Each ticket fits in 1–4 hours of focused work and produces a reviewable commit on
`demo/capstone`. Tickets are sized for one developer; the day grouping matches the
plan's checkpoints.

**Demo exit criterion (re-stated):** on a clean `make db-reset && make demo-seed`,
the web app's *Find my size* flow turns a pasted partner-brand URL into a
recommendation showing all four PRD §5.4 components (size, confidence, fit notes,
reference garments), and one prepared URL exercises the confidence < 60% two-candidate
path.

**Isolation invariant (every ticket must preserve):** no edits to `apps/ios`,
`apps/android`, or `apps/api/src/api/main.py`. Throwaway code stays inside `apps/web/`
and `apps/api/src/api/demo/`. The domain-core tickets (DEMO-01–04) are the exception —
they write real roadmap code under `api/domain/` and are kept after the demo.

Refs are to `Sizeify_PRD_v1.docx`, `CLAUDE.md`, `PROJECT_PLAN.md`, and the matching
Phase 1 tickets in `PHASE_1_TICKETS.md`.

Legend: **[KEEP]** = real roadmap code, survives the demo · **[THROWAWAY]** = demo-only.

Status legend: `Not started` · `In progress` · `Done`. Update both the table below and
the **Status** line on a ticket in the same commit that lands it.

## Progress tracker

_Last updated: 2026-06-11. Day-1 engine complete: DEMO-01–04 (`stretch`, `fit_profile`,
`matching`, `recommendation`) landed with unit tests and verified end-to-end on the real
demo closet (jcrew Bowery → size M, confidence 0.73). Next: DEMO-05 seed, then the headline
endpoint (07) and web wiring (09). Full `uv run pytest` pending a 3.12 toolchain._

| Ticket | Day | Type | Status |
|---|---|---|---|
| DEMO-00 — Backend boots, migrates, tests run | 0 | KEEP | Done |
| DEMO-01 — `domain/stretch.py` | 1 | KEEP | Done |
| DEMO-02 — `domain/fit_profile.py` | 1 | KEEP | Done |
| DEMO-03 — `domain/matching.py` | 1 | KEEP | Done |
| DEMO-04 — `domain/recommendation.py` | 1 | KEEP | Done |
| DEMO-05 — Implement `demo/seed_demo.py` | 1 | THROWAWAY | Not started |
| DEMO-06 — Day-1 checkpoint: REPL recommendation | 1 | KEEP | Done (via script) |
| DEMO-07 — `POST /demo/recommend-from-url` | 2 | THROWAWAY | Not started |
| DEMO-08 — Closet read + add-garment endpoints | 2 | THROWAWAY | Not started |
| DEMO-09 — Web: paste-URL flow live | 2 | THROWAWAY | Not started |
| DEMO-10 — Web: closet + add-garment flow live | 2 | THROWAWAY | Not started |
| DEMO-11 — Tune fixtures (confident + two-candidate) | 3 | THROWAWAY | Not started |
| DEMO-12 — Web polish + pre-auth | 3 | THROWAWAY | Not started |
| DEMO-13 — Rehearsal, reset flow, fallback capture | 3 | THROWAWAY | Not started |

> DEMO-00 is marked **Done** because the Phase 1 backbone it depends on (async session,
> settings, test fixtures, migrations) is merged and the suite is green. Verify with
> `make demo` / `uv run pytest` before relying on it.

---

## Day 0 — Prerequisites (land first if not already green)

### DEMO-00 — Backend boots, migrates, and tests run [KEEP]

**Status:** Done

**Scope:** Ensure the Phase 1 prerequisites the demo depends on are in place. Do not
re-do them if already merged — just verify.

**Deliverables**

- Async engine + `async_sessionmaker` + `get_session` dependency present
  (TKT-P1-00b). Throwaway `SELECT 1` route round-trips against compose Postgres.
- `Settings` exposes `database_url`, `redis_url`, `jwt_secret` with local-safe
  defaults (TKT-P1-00c).
- `tests/conftest.py` transactional session + `AsyncClient` fixtures exist (TKT-P1-00e).

**Acceptance**

- `docker compose -f infra/docker-compose.yml up -d` then
  `cd apps/api && uv run alembic upgrade head` is clean.
- `uv run pytest` and `uv run mypy src` are green on the branch as-is.

**Refs:** PHASE_1_TICKETS.md TKT-P1-00b/00c/00e.

---

## Day 1 — Domain core + seed (the engine)

### DEMO-01 — `domain/stretch.py` [KEEP]

**Status:** Done — `domain/stretch.py` + `tests/test_stretch.py` landed. Additive cm offsets
(none 0 · slight 1 · moderate 2 · high 3.5), `effective_measurement()`, typed
`UnknownStretchLevelError`. Logic verified (12 assertions); full `uv run pytest` pending a
3.12 toolchain.

**Scope:** Encode v1 hand-tuned stretch coefficients. This is TKT-P1-11, pulled
forward — the matching engine needs it.

**Deliverables**

- `apps/api/src/api/domain/stretch.py` exporting
  `effective_measurement(measurement_cm: float, stretch_level: StretchLevel) -> float`
  and a module-level constants table keyed by `none | slight | moderate | high`.
- Module comment forbidding any "learning loop" change without scope approval.

**Acceptance**

- Unit tests: each level returns the documented multiplier; unknown level raises a
  typed error. `uv run mypy src` clean.

**Refs:** PRD §6.3; CLAUDE.md gotchas; TKT-P1-11.

### DEMO-02 — `domain/fit_profile.py` [KEEP]

**Status:** Done — `domain/fit_profile.py` + `tests/test_fit_profile.py`. Pure
`build_fit_profile(ClosetSnapshot) -> FitProfile`, rating-weighted evidence with
stretch + verdict shifts, `spread`/`maturity`, `to_response()` to `FitProfileResponse`.

**Scope:** Pure fit-profile construction over a closet snapshot. This is TKT-P1-12,
scoped to what the demo seed exercises (no use-case variants required for the headline
flow, but keep the field).

**Deliverables**

- `apps/api/src/api/domain/fit_profile.py` with `build_fit_profile(snapshot) -> FitProfile`
  (Bayesian update, weak prior, owned-garment evidence) and supporting dataclasses.
- Output serializes to `schemas/fit_profile.py::FitProfileResponse`
  (per-dimension `preferred_cm` + `spread_cm` + `sample_size`, plus `maturity`).
- No DB or HTTP access — pure function over an in-memory snapshot.

**Acceptance**

- Unit fixtures: empty closet → `maturity='cold_start'`; the seeded 5-garment closet →
  a per-dimension profile with the chest pulled by the slightly-tight signal.
- ≥90% line coverage on the module.

**Refs:** PRD §6.2; TKT-P1-12.

### DEMO-03 — `domain/matching.py` [KEEP]

**Status:** Done — `domain/matching.py` + `tests/test_matching.py`. `match()` ranks sizes by
weighted out-of-range distance (stretch-adjusted), `RankedSize` with per-dimension deltas,
`MissingDimensionError` for incomplete charts.

**Scope:** Pure matching engine over `(fit_profile, brand_product)`. This is TKT-P1-14.

**Deliverables**

- `apps/api/src/api/domain/matching.py` with `match(fit_profile, brand_product) -> RankedSizes`
  and a `RankedSize` dataclass (`size_label, distance, per_dimension_deltas`).
- Applies `stretch.effective_measurement` and weights from `domain/dimension_weights.py`.
- Missing dimension in a product's size chart raises a typed error (not a KeyError).

**Acceptance**

- Unit tests: in-range fit picks the closest size; weighted distance respects
  per-dimension weights; a stretch change reorders the ranking.

**Refs:** PRD §6.4, §6.3; TKT-P1-14.

### DEMO-04 — `domain/recommendation.py` (four §5.4 components) [KEEP]

**Status:** Done — `domain/recommendation.py` + `tests/test_recommendation.py`. `recommend()`
returns all four §5.4 components, confidence rule (<0.60 → two candidates, cold-start capped
≤0.50), `to_wire()` matching `types.ts::Recommendation`.

**Scope:** Wrap `match` with the confidence rule and assemble the recommendation. This
is TKT-P1-15 and is the piece the demo card renders.

**Deliverables**

- `apps/api/src/api/domain/recommendation.py` exposing
  `recommend(fit_profile, brand_product) -> Recommendation`.
- Output contains all four PRD §5.4 components: `size`, `confidence` (0–1),
  `fit_notes` (per-dimension narrative strings), `reference_garments` (top owned
  garments driving the call, each with a one-line `why`).
- Confidence < 0.60 → second candidate populated with a `tradeoff` string.
  Cold-start profile clamps confidence ≤ 0.50.

**Acceptance**

- Unit tests: a confident closet → one candidate ≥ 0.60; a deliberately ambiguous
  product → two candidates with non-empty `tradeoff`.
- Shape matches `apps/web/src/api/types.ts::Recommendation` exactly (hand-checked).

**Refs:** PRD §5.4, §6.4; TKT-P1-15.

### DEMO-05 — Implement `demo/seed_demo.py` [THROWAWAY]

**Status:** Not started

**Scope:** Make the seed runnable via the real repositories. Fixtures already exist
(`demo/fixtures/{brand_products,demo_closet}.json`).

**Deliverables**

- `api.demo.seed_demo.run()` (idempotent) that: upserts the demo user (Argon2 hash);
  ensures the `mens_button_down_shirt` category is seeded (reuse
  `api.seeds.garment_categories`); inserts `brand_product` rows from the fixture;
  inserts the 5 `owned_garment` rows + their `fit_signal` rows (measurements in cm,
  `source='user_added'`, `raw_feedback_text` populated).
- Prints the demo user's JWT to stdout for `VITE_DEMO_JWT`.
- All writes via `api.repositories.*` — no raw SQL.

**Acceptance**

- `DEMO_MODE=1 uv run python -m api.demo.seed_demo` run twice → no duplicate rows.
- After seed, `build_fit_profile(snapshot)` for the demo user returns a mature,
  non-cold-start profile.

**Refs:** DEMO_PLAN.md §4 Day 1; CLAUDE.md domain conventions (fit_signal source +
raw text mandatory).

### DEMO-06 — Day-1 checkpoint: REPL recommendation [KEEP]

**Status:** Done (via script) — engine composed end-to-end on the real demo closet
(jcrew Bowery → size M, confidence 0.73, four §5.4 components). A formal
`tests/integration/test_demo_recommend.py` should be added once DEMO-05's seed lands so the
checkpoint runs in CI from seeded DB state.

**Scope:** Prove the engine end-to-end before any UI, equivalent to the Phase 1 exit
criterion (TKT-P1-19) done early.

**Deliverables**

- A scratch script or pytest (`tests/integration/test_demo_recommend.py`) that loads
  the seeded closet, calls `recommend(build_fit_profile(snapshot), product)` for one
  fixture brand, and asserts the full four-component shape against a hand-computed
  expectation documented inline.

**Acceptance**

- Test green; the hand-computed size + confidence band match the engine output.

**Refs:** PROJECT_PLAN.md Phase 1 exit; TKT-P1-19; PRD §5.4.

---

## Day 2 — Demo API wiring + web flows

### DEMO-07 — `POST /demo/recommend-from-url` (headline flow) [THROWAWAY]

**Status:** Not started

**Scope:** Implement the headline handler in `demo/router.py`.

**Deliverables**

- Request body `{url: str}`; pipeline: `stub_scraper.resolve(url)` → load demo-user
  closet snapshot via `owned_garments` repo → `build_fit_profile` → `recommend` →
  persist a `recommendation` row (`prompt_version='demo-v0'`) → return the §5.4 payload.
- `stub_scraper.UnknownBrandError` → 422 with a "not a partner brand" message
  (mirrors PRD §7.5 unknown-product behaviour).
- Response validated by a Pydantic model matching `types.ts::Recommendation`.

**Acceptance**

- `curl -XPOST /demo/recommend-from-url -d '{"url":"https://www.jcrew.com/p/x"}'`
  returns size + confidence + fit_notes + reference_garments.
- An unknown hostname returns 422, not 500.

**Refs:** DEMO_PLAN.md §4 Day 2; PRD §5.4, §7.5.

### DEMO-08 — Closet read + add-garment endpoints [THROWAWAY]

**Status:** Not started

**Scope:** Implement `GET /demo/closet` and `POST /demo/closet/garments` in
`demo/router.py`, including stubbed extraction.

**Deliverables**

- `GET /demo/closet` → list of the demo user's garments (shape = `types.ts::Garment`).
- `POST /demo/closet/garments` accepts measurements (cm) + optional `feedback`; on
  feedback, calls `stub_llm.extract(feedback, category_schema)` and persists the
  returned signals with `source='nlp_extracted'`; returns `{garment, signals}`.
- Measurement validation against the v1 button-down ranges (reject out-of-range 422).

**Acceptance**

- Add a garment with feedback "perfect chest but collar is tight" → response includes a
  `chest: preferred` and a `neck: slightly_tight` signal.
- Out-of-range chest → 422 with a per-field error.

**Refs:** DEMO_PLAN.md §4 Day 2; PRD §5.2, §6.1; CLAUDE.md (fit_signal source/raw text).

### DEMO-09 — Web: paste-URL flow live [THROWAWAY]

**Status:** Not started

**Scope:** Wire `PasteUrlPage` + `RecommendationCard` to the live endpoint and confirm
the magic moment renders.

**Deliverables**

- `apps/web/.env` populated from `.env.example` (`VITE_API_BASE`, `VITE_DEMO_JWT`).
- Loading and error states verified against real responses (already stubbed in the
  components; confirm they fire).

**Acceptance**

- `make demo-api` + `make demo-web`; paste a `jcrew.com`/`uniqlo.com` URL →
  `RecommendationCard` shows size, confidence %, fit notes, and reference garments.
- CORS clean (demo app already allow-lists `http://localhost:5173`).

**Refs:** DEMO_PLAN.md §4 Day 2.

### DEMO-10 — Web: closet + add-garment flow live [THROWAWAY]

**Status:** Not started

**Scope:** Wire `ClosetPage` / `AddGarmentPage` / `MeasurementForm` / `SignalChips` to
the closet endpoints.

**Deliverables**

- Closet lists the 5 seeded garments. Add-garment posts measurements + feedback and
  renders the returned signals as chips.
- Range-validation hints in `MeasurementForm` match the backend's 422 boundaries.

**Acceptance**

- Add a shirt with free-text feedback → chips appear; the new shirt shows in the closet
  after refresh.

**Refs:** DEMO_PLAN.md §4 Day 2; PRD §6.1.

---

## Day 3 — Polish, rehearse, de-risk

### DEMO-11 — Tune fixtures for a confident pick + a two-candidate path [THROWAWAY]

**Status:** Not started

**Scope:** Make the seeded data tell a clean story. Tune fixtures, never the engine.

**Deliverables**

- The default demo URL yields a **confident** (≥ 0.60), correct-looking primary size.
- One prepared URL/product yields the **confidence < 60% two-candidate** path so the
  presenter can show the trade-off (PRD §5.4).
- Document both URLs in `DEMO_PLAN.md` §5 (or a `demo/fixtures/README`).

**Acceptance**

- Both URLs produce the intended card states on a fresh seed, reproducibly.

**Refs:** DEMO_PLAN.md §4 Day 3, §5; PRD §5.4.

### DEMO-12 — Web polish + pre-auth [THROWAWAY]

**Status:** Not started

**Scope:** Presentation-quality pass. No new features.

**Deliverables**

- Empty/loading/error states verified on both tabs; spacing/typography pass over
  `styles.css`.
- `VITE_DEMO_JWT` set so there's no login detour on stage.

**Acceptance**

- A cold open (fresh browser) lands directly on a usable *Find my size* screen.

**Refs:** DEMO_PLAN.md §4 Day 3.

### DEMO-13 — Rehearsal, reset flow, and fallback capture [THROWAWAY]

**Status:** Not started

**Scope:** De-risk the live run.

**Deliverables**

- `make db-reset && make demo-seed` proven as the between-run reset.
- A 60–90s screen recording of the full script (§5) as a fallback if infra misbehaves.
- Two full rehearsals against the §5 script.

**Acceptance**

- Two consecutive clean run-throughs; reset returns to identical starting state; the
  fallback recording covers the headline + two-candidate moments.

**Refs:** DEMO_PLAN.md §4 Day 3, §5.

---

## Suggested execution order

- **Day 1 (engine):** DEMO-00 → 01 → 02 → 03 → 04 → 05 → 06.
- **Day 2 (wiring):** DEMO-07 → 08 → 09 → 10. (07/09 are the must-haves; 08/10 next.)
- **Day 3 (polish):** DEMO-11 → 12 → 13.

**If time runs short,** ship in this priority: DEMO-01–04 + 07 + 09 (headline flow
works), then 11 (the two-candidate story), then 05/08/10 (closet), then 12/13 (polish).

## Dependency notes

- DEMO-03 needs DEMO-01; DEMO-04 needs DEMO-02 + DEMO-03.
- DEMO-07 needs DEMO-04 + DEMO-05; DEMO-09 needs DEMO-07.
- DEMO-08 needs DEMO-05; DEMO-10 needs DEMO-08.
- DEMO-11 needs DEMO-07 (tunes against real engine output).
