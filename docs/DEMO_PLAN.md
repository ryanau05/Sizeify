# Sizeify — 3-Day Capstone Demo Plan

**Goal:** a working prototype that lands Sizeify's core claim — *closet in, correct
size out* — for a capstone course-series application, in 3 days, **without
disturbing the long-term native iOS/Android roadmap**.

**Branch:** `demo/capstone` (off `main`). All demo work is additive and namespaced.
**Authoritative roadmap (unchanged):** `PROJECT_PLAN.md`, `PHASE_1_TICKETS.md`, `Sizeify_PRD_v1.docx`.

This plan is throwaway. When the demo is done, `PROJECT_PLAN.md` is still the source
of truth and the demo branch can be archived or cherry-picked from (see §6).

---

## 1. What the demo shows (and what it doesn't)

Decided up front:

- **Frontend:** a lightweight web app (`apps/web`, Vite + React + TS). It is the web
  stand-in for the native share-sheet — paste a product URL instead of sharing it.
- **Two flows:** (a) **paste-URL → recommendation** (the headline moment) and
  (b) **closet + measurement entry** (the data that powers it, incl. free-text
  feedback → structured fit signals).
- **Data fidelity:** seeded + stubbed. A pre-seeded 5-garment closet and brand size
  charts; the scraper and LLM edges are stubbed so the demo is deterministic,
  offline-safe, and free of live-API risk during the presentation.

**Deliberately out of scope for the demo** (and why it's fine to skip):

| Skipped | Why it's safe to skip for a demo |
|---|---|
| Native iOS/Android apps | Share extensions / rich push are platform-specific and can't be built in 3 days (Phases 4–6). The web client proves the same core. |
| Live scrapers (Phase 2) | Replaced by a fixture lookup so no retailer-HTML fragility on stage. |
| Live LLM extraction (Phase 3) | Replaced by canned signal extraction — no API cost, no latency variance. |
| Auth UI, GDPR flows, push, outcome tracking | Not needed to demonstrate the value prop; all remain on the real roadmap. |

**Non-negotiable even in the demo:** every recommendation renders all four PRD §5.4
components — size, confidence, fit notes, reference garments — and confidence < 60%
shows two candidates. That contract *is* the product; don't cut it.

---

## 2. Isolation strategy (how the demo avoids touching the roadmap)

The whole point of the structure is that a reviewer of the long-term repo sees the
demo as a clean, removable appendage.

1. **Separate branch.** `demo/capstone` off `main`. Nothing merges back to `main`
   unless deliberately cherry-picked (§6).
2. **Production entrypoint untouched.** `api/main.py` is not edited. The demo runs a
   *separate* ASGI app, `api.demo.app:app`, which imports `create_app()` and bolts on
   a `/demo` router. It refuses to start without `DEMO_MODE=1`.
3. **Throwaway code is quarantined** in two directories only:
   - `apps/web/` — the demo web client (sibling to `apps/ios`, `apps/android`; shares nothing).
   - `apps/api/src/api/demo/` — stubbed scraper, stubbed LLM, seed, demo router.
   Deleting both, plus `docs/DEMO_PLAN.md` and the `demo*` Makefile targets, removes
   the demo entirely. `git grep DEMO` finds every touchpoint.
4. **Real domain core is advanced, not forked.** The demo calls the same
   `api.domain.{fit_profile,matching,recommendation}` functions that Phase 1 Track B
   ships. So the most valuable work done this week (the matching engine) is **real
   roadmap progress**, not demo scaffolding. Only the edges are stubbed.
5. **No new DB tables.** Seeding uses the real models/migrations/repositories.

What this means: the only "wasted" code is the web client and the two edge stubs.
Everything else moves Phase 1 forward.

---

## 3. Current starting point (verified in repo)

- Phase 0 complete. Backend models, repositories, Pydantic schemas, auth (Argon2 +
  JWT), and migrations `0001`/`0002` exist (uncommitted Phase 1 work, carried onto
  this branch).
- **Gap that matters for the demo:** the domain core is not built yet. `api/domain/`
  has only `dimension_weights.py`. The demo needs `stretch.py`, `fit_profile.py`,
  `matching.py`, and `recommendation.py` — these are Phase 1 tickets
  TKT-P1-11/12/14/15, so building them now is on-plan.

---

## 4. Day-by-day plan

Effort assumes one developer. Each day ends with something demoable.

### Day 1 — Backend spine + the real domain core

**Outcome:** the matching engine produces a correct recommendation from a seeded
closet, callable from a Python REPL. No UI yet.

- Land the Phase 1 prerequisites if not already green: async engine + `get_session`,
  `Settings`, test fixtures (TKT-P1-00b/c/e). Confirm `alembic upgrade head` is clean.
- Implement the domain core (these are real Phase 1 deliverables):
  - `domain/stretch.py` (TKT-P1-11) — hand-tuned coefficients, `effective_measurement`.
  - `domain/fit_profile.py` (TKT-P1-12) — `build_fit_profile(snapshot)`.
  - `domain/matching.py` (TKT-P1-14) — `match(profile, brand_product)`.
  - `domain/recommendation.py` (TKT-P1-15) — `recommend(...)`, confidence rule,
    two-candidate output, all four §5.4 components.
- Fill in `api/demo/seed_demo.py` using the real repositories + the fixtures already
  scaffolded (`fixtures/demo_closet.json`, `fixtures/brand_products.json`).
- **Checkpoint:** seed the DB, then in a REPL call `recommend(build_fit_profile(...),
  product)` and hand-verify the size + confidence look right. This is essentially the
  Phase 1 exit-criterion test (TKT-P1-19) done early.

### Day 2 — Wire the demo API + the headline flow end-to-end (no polish)

**Outcome:** `POST /demo/recommend-from-url` returns a real recommendation; the web
app renders it.

- Implement the four handlers in `api/demo/router.py`:
  - `recommend-from-url`: `stub_scraper.resolve(url)` → load closet snapshot via repo
    → `build_fit_profile` → `recommend` → persist (`prompt_version='demo-v0'`) →
    return the §5.4 payload (already typed in `apps/web/src/api/types.ts`).
  - `seed`, `GET /closet`, `POST /closet/garments` (with `stub_llm.extract` on feedback).
- Run `make demo-api` + `make demo-web`. Paste a `jcrew.com`/`uniqlo.com` URL, confirm
  `RecommendationCard` shows size, confidence, fit notes, reference garments.
- Wire the closet page + add-garment flow against live endpoints. Confirm free-text
  feedback → `SignalChips`.
- **Checkpoint:** the two flows work locally, ugly but real.

### Day 3 — Polish, rehearse, de-risk

**Outcome:** a demo you can run live, twice, without surprises.

- Make the seeded closet produce a *confident, correct-looking* primary size, and pick
  one URL that triggers the **two-candidate** (confidence < 60%) path so you can show
  the nuance. Tune the fixture, not the engine.
- Visual pass on the web app (spacing, loading state, empty/error states already
  stubbed in `styles.css`).
- Pre-auth the web client: paste the seeded JWT into `apps/web/.env`
  (`VITE_DEMO_JWT`) so there's no login detour on stage.
- **Record a 60–90s screen capture as a fallback** in case live infra misbehaves.
- Rehearse against the script in §5. Reset with `make db-reset && make demo-seed`
  between run-throughs.
- Buffer time for the inevitable: CORS, a fixture typo, a port clash.

**If you fall behind:** the headline paste-URL flow (Day 2 first bullet) is the one
thing that must work. Closet entry, the two-candidate path, and visual polish are, in
that order, the things to drop.

---

## 5. Demo script (≈3 minutes)

1. **The problem (20s).** "Every brand's 'medium' is different. You own shirts that
   fit — Sizeify uses them to tell you what size to buy in a brand you've never tried."
2. **The closet (30s).** Open *My closet* — five shirts the user owns, with
   measurements and how they fit. (Optional: add one live to show measurement +
   "how does it fit?" → structured chips.)
3. **The magic moment (40s).** *Find my size* → paste a partner-brand shirt URL →
   recommendation appears: **size, confidence %, why (fit notes), and which of your
   shirts it's based on.**
4. **The nuance (30s).** Paste the low-confidence URL → two candidates with the
   trade-off. "When we're not sure, we say so and show both — we don't fake
   confidence." (Maps to PRD §5.4.)
5. **The vision (30s).** "In production this is a native share-sheet: share any shirt
   from Safari and get a push notification in under 3 seconds. The recommendation
   engine you just saw is the real one — the roadmap is the native shell, ten live
   brand scrapers, and an LLM that turns 'a bit snug in the chest' into structured
   signals." (Point to `PROJECT_PLAN.md`.)

The last line is the capstone hook: a working core today, a credible, scoped path to a
real product.

---

## 6. After the demo

- **Keep the domain core.** `domain/stretch|fit_profile|matching|recommendation.py`
  and the seed fixtures are real Phase 1 work — cherry-pick them onto a Phase 1 PR
  branch and close TKT-P1-11/12/14/15/19.
- **Discard the rest.** `apps/web/` and `apps/api/src/api/demo/` can be deleted or left
  on the `demo/capstone` branch indefinitely; they never reach `main` or production.
- **Do not** let demo shortcuts leak into the roadmap: the stubbed scraper/LLM are not
  the Phase 2/3 designs, the web client is not the mobile client, and `prompt_version
  ='demo-v0'` is a sentinel. The PRD scope rules (men's button-downs, 10 brands,
  single user) still hold.

---

## 7. Structure created on this branch

```
docs/DEMO_PLAN.md                      ← this file
scripts/demo.sh                        ← one-command demo bootstrap
Makefile                               ← + demo / demo-api / demo-web / demo-seed targets
apps/web/                              ← THROWAWAY web client (Vite + React + TS)
  src/{App,main}.tsx, styles.css
  src/api/{client,types}.ts
  src/pages/{PasteUrl,Closet,AddGarment}Page.tsx
  src/components/{RecommendationCard,MeasurementForm,SignalChips}.tsx
apps/api/src/api/demo/                 ← THROWAWAY backend demo layer (flag-gated)
  app.py            ← separate ASGI app; refuses to run without DEMO_MODE=1
  router.py         ← /demo/* endpoints (stubs to fill Day 2)
  stub_scraper.py   ← fixture lookup, stands in for Phase 2
  stub_llm.py       ← canned extraction, stands in for Phase 3
  seed_demo.py      ← seeds demo user/closet/products via REAL repos
  fixtures/{brand_products,demo_closet}.json
```

Real domain work to add during the demo (kept after — see §6):
`apps/api/src/api/domain/{stretch,fit_profile,matching,recommendation}.py`.
