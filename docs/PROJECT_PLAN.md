# Sizeify v1 — Project Plan

This document is the implementation playbook for Sizeify v1. It translates the PRD into a phased build plan with concrete deliverables, owners, sequencing, and decision points. Read it alongside `Sizeify_PRD_v1.docx` (authoritative spec) and `FILE_STRUCTURE.md` (repo layout).

The PRD's scope discipline is unusual and load-bearing: men's button-down shirts only, ten partner brands, single-user. Every section below assumes those constraints. If a piece of work in here implies something broader, treat that as a bug in the plan, not a feature.

---

## 1. Goals and constraints, restated

The point of v1 is to prove one claim: *given a closet of garments a user has measured and rated, we can recommend the right size in any of ten partner brands, in under three seconds, with ≥80% correctness over three months of live use.*

Everything is measured against five PRD-defined targets:

| Target | Source | Implication for the build |
|---|---|---|
| ≥80% recommendation correctness over 3 months | PRD §3.1, §12.1 | Outcome tracking, calibration plots, and prompt-version traceability are first-class features, not analytics afterthoughts. |
| Share-sheet → notification: p50 < 3s, p95 < 5s | PRD §3.1, §7.3, §9.2 | Async everywhere on the share-sheet path. Per-step latency budgets (§9.2) are non-negotiable; CI must surface regressions. |
| 10 brands shippable | PRD §7.6 | Each brand needs a scraper module, fixture set, and daily synthetic run. No one-off scripts. |
| First garment in <4 min | PRD §3.1, §10.1 | Onboarding UX gets dedicated polish time; measurement entry must be forgiving. |
| Every recommendation has size + confidence + fit notes + reference garments | PRD §5.4 | The recommendation contract is the API response shape and the notification payload — both validated. |

All other "nice-to-haves" are deferred. The non-goals list in PRD §3.2 is the firewall.

---

## 2. Architecture summary

Four independently deployable components, mirrored in the repo as four top-level apps. The boundaries are intentional — they keep the share-sheet hot path lean and let scraper churn stay isolated from product code.

**Mobile clients (iOS + Android, native).** Closet management, guided measurement input, share-extension entry point, recommendation rendering. Native because share extensions, share intents, and rich notifications are all platform-specific (PRD §9.5).

**Backend API (Python 3.12 + FastAPI).** Stateless. Owns fit-profile construction, the matching engine, the `/recommend` endpoint, outcome tracking, and push dispatch. PostgreSQL 16 for state, Redis for hot caches once measured to be needed.

**Scraper service.** Per-brand modules behind a uniform interface (PRD §9.3). Daily refresh of known products plus on-demand scrapes of unknown URLs. Runs as worker tasks behind an `arq` queue on Redis so it can scale independently and not block API workers.

**LLM extraction service.** Thin proxy in front of the Anthropic API. Owns prompt templates (versioned, in-repo), retries, schema validation, and a contract that *only the free-text feedback and the category schema cross the boundary* — no PII, no closet dump (PRD §11).

The hot path is documented in PRD §9.2 with explicit budgets per step. Memorize them — every PR on the share-sheet path should answer "what does this do to the budget?"

```
share extension  →  POST /recommend  (URL + JWT)
                     │
                     ├─ resolve URL          200 ms
                     ├─ brand_product cache  ─ hit? skip
                     │     miss → scraper    1500 ms
                     ├─ fit profile build    100 ms  (cached)
                     ├─ matching engine      300 ms
                     └─ persist + push       500 ms
                                             ────────
                                             2.5 s p50, 3 s SLO
```

---

## 3. Data model summary

The PRD's §8 schema is the source of truth. Six core entities:

`user` · `garment_category` · `owned_garment` · `fit_signal` · `brand_product` · `recommendation`

Three things deserve emphasis because they trip people up later:

1. **Measurements are stored in cm internally.** Display preference is a UI concern. Convert at the boundary, not in the database. (CLAUDE.md domain conventions.)
2. **`fit_signal.source` and `fit_signal.raw_feedback_text` are mandatory** on every row. They exist so we can debug prompt drift. Do not let a "minor refactor" strip them.
3. **`recommendation.prompt_version`** is required even though it isn't called out in §8 — it's required by CLAUDE.md's domain conventions for detecting LLM-extraction drift across prompt updates. Add it to the schema in the first migration.

Indexes from PRD §8.2 go in the same migration. They are not optional; the share-sheet path can't afford a sequential scan.

---

## 4. Phased build plan

Effort is given in solo-developer-weeks. Treat the numbers as relative sequencing aids, not deadlines. Many phases run in parallel; explicit dependencies are called out.

### Phase 0 — Foundation (week 1)

Goal: a runnable monorepo with the four-component skeleton, lint/format/test wired, CI green on an empty repo.

Deliverables:
- Monorepo per `FILE_STRUCTURE.md`.
- `apps/api` boots with FastAPI, returns 200 on `/health`. `uv` managed, `ruff` and `mypy` clean.
- `apps/ios` workspace builds (`xcodebuild build`) and runs an empty SwiftUI screen.
- `apps/android` builds debug APK with an empty Compose screen.
- `docker-compose.yml` brings up Postgres 16 and Redis. `alembic upgrade head` runs against an empty schema.
- Pre-commit hook installs and runs lint/format on staged files.
- GitHub Actions workflows: backend (lint + test + mypy), iOS (build), android (build + ktlintCheck).

Exit criterion: a fresh clone + `make bootstrap` (or equivalent) gets a contributor to "all green" in under ten minutes.

### Phase 1 — Backend core: schema, auth, closet CRUD (weeks 2–3)

Goal: a backend that can store a user's closet and run the matching engine in isolation.

> **Demo cross-reference:** the `demo/capstone` branch (`docs/DEMO_PLAN.md`, `docs/DEMO_TICKETS.md`) pulls the domain-core pieces of this phase forward as tickets DEMO-01–04, which map to TKT-P1-11/12/14/15 (`stretch`, `fit_profile`, `matching`, `recommendation`). Those are *real* Phase 1 deliverables — when the demo lands them, cherry-pick onto a Phase 1 PR branch rather than rebuilding. Everything else under `apps/api/src/api/demo/` and `apps/web/` is throwaway.

Deliverables:
- Alembic migration creating all six entities from PRD §8 plus `recommendation.prompt_version`. Indexes from §8.2.
- Repository layer (`apps/api/src/api/repositories/`) wrapping every entity. No raw `AsyncSession` in route handlers — that boundary is in CLAUDE.md and it is load-bearing for tests.
- Pydantic models for every request and response body. Reject raw dicts in route handlers in code review.
- Auth: email + password via Argon2; JWT issuance with rotation. Sign-in-with-Apple and Google deferred to Phase 4 polish.
- Endpoints:
  - `POST /auth/signup`, `POST /auth/login`, `POST /auth/refresh`
  - `GET/POST/PATCH/DELETE /closet/garments`
  - `POST /closet/garments/{id}/fit-signals` (manual entry path for v1)
  - `GET /closet/fit-profile` (returns the constructed fit profile for the user)
- Fit-profile construction (PRD §6.2) implemented as a pure function over a closet snapshot. Bayesian update with weak prior, owned-garment evidence, use-case conditioning. Unit tests with crafted closets covering: empty closet, single love-rated garment, contradictory signals, use-case-specific signals.
- Matching engine (PRD §6.4) implemented as a pure function over (fit profile, brand_product). Stretch adjustment (PRD §6.3) using v1's hand-tuned coefficients in `apps/api/src/api/domain/stretch.py`. Unit tests covering: in-range fit, weighted distance ranking, confidence < 60% returning two candidates per PRD §5.4, gap-to-second-best driving confidence.
- GDPR/CCPA endpoints from day one (PRD §11): `GET /me/export` (full data dump as JSON), `DELETE /me` (cascade delete), consent flag on signup.

Exit criterion: integration test seeds a closet with five garments, calls the matching engine with a fixture `brand_product`, asserts the recommendation matches a hand-computed expectation including all four mandatory components.

### Phase 2 — Scrapers: interface, ten modules, fixtures (weeks 3–4, parallelizable with Phase 1)

Goal: ten brands scraped reliably, with a daily synthetic test detecting HTML drift before users do.

Deliverables:
- `apps/api/src/scrapers/base.py` defines the uniform interface (PRD §9.3). Every brand module conforms.
- One module per brand from PRD §7.6: Uniqlo, J.Crew, Bonobos, Everlane, Banana Republic, Brooks Brothers, Charles Tyrwhitt, Mr. Porter, Spier & Mackay, Proper Cloth.
- Each module has a fixture file with at least 5 known products and their expected size charts. CI runs scrapers against fixtures (frozen HTML) on every PR. A separate scheduled job runs them against live URLs daily and pages the on-call channel on diff.
- `arq` worker consumes scrape jobs from Redis. The `/recommend` path enqueues an on-demand scrape on cache miss and `await`s the result with a 1.5s timeout — beyond the timeout we serve "couldn't find this exact product" per PRD §7.5.
- URL resolver (`apps/api/src/api/services/url_resolver.py`) handles redirects + Instagram/TikTok wrapper URLs. Hard 2s timeout; fail fast.
- Brand discriminator: hostname-driven dispatch with a small allowlist. Unknown hostnames short-circuit to "unknown brand" notification.
- Playwright is *only* loaded in the modules that genuinely need JS rendering. Don't drag a headless browser into modules that don't need it (CLAUDE.md scrapers section).

Exit criterion: `uv run python -m scrapers.cli --brand <each> --url <fixture URL>` returns a complete `BrandProduct` with size chart for every brand. The daily synthetic run is green for three consecutive days.

### Phase 3 — LLM extraction service (week 4, parallelizable)

Goal: free-text fit feedback → structured fit signals, reliably and reviewably.

Deliverables:
- `apps/api/src/llm/` with a single public function `extract_fit_signals(feedback, category_schema, measurements) -> ExtractionResult`.
- Prompt template stored as `prompts/fit_extraction/v1.md` with explicit version tag. **Changing a prompt means bumping the version, not editing in place** (CLAUDE.md workflow rules). Old `fit_signal` rows must remain traceable to the prompt that produced them.
- Few-shot examples from PRD §6.1 (the collar/body/sleeve example) plus 8–12 additional examples covering: magnitude estimates, use-case conditions, contradictory signals, ambiguous wording, off-topic feedback (which should land in `unparsed_notes`).
- Schema validation on every response. Malformed JSON → deterministic error + retry once. Two failures → return `unparsed_notes` containing the raw feedback so the user can edit manually.
- Contract enforced by tests: only `feedback`, `category_schema`, and `measurements` cross the boundary. No email, no closet dump, no user ID. (PRD §11 privacy rule, CLAUDE.md privacy rules.)
- Endpoint `POST /closet/garments/{id}/fit-feedback` accepts free text, runs extraction, persists `fit_signal` rows with `source='nlp_extracted'`, and returns the extracted signals for client-side review. Client `PATCH`es to mark `source='user_edited'` after review.

Exit criterion: a regression suite of 30+ feedback strings produces structurally-valid extractions that match expected signals on ≥90% of cases.

### Phase 4 — iOS client (weeks 5–7, parallelizable with Phase 5)

Goal: native iOS app that handles the full PRD §10 surface set, minus share extension (Phase 6).

Deliverables, week by week:

*Week 5 — scaffolding and onboarding.* Tab-bar shell with three tabs (Closet, Recent, Profile). Onboarding flow per PRD §10.1: welcome → account → stated fit preference → measurement tutorial → first garment → prompt for 2–4 more → push permission. Auth hooked up to backend.

*Week 6 — closet and measurement.* Closet list with grouping by category. Add-garment flow with guided measurement screens for each dimension (chest, body length, shoulder, sleeve, neck, cuff). Validation against expected ranges (PRD §5.2). Free-text fit feedback screen → extraction request → reviewable chips with edit/add/delete (PRD §6.1).

*Week 7 — recommendation surfaces and polish.* Recent recommendations list. Recommendation detail screen per PRD §10.3. Outcome tracking prompt at 14 days. Profile screen with fit-profile visualization.

Cross-cutting iOS rules:
- Measurements stored as cm; user's display preference is a UI-layer setting only.
- JWT lives in Keychain with `kSecAttrAccessibleAfterFirstUnlock` so the share extension can read it after first unlock.
- App Group entitlement set up *now* — share extension target in Phase 6 will need it.
- Camera permission and photo storage entitlements wired up even though v1 photos are optional. Required so we don't preclude the CV-assist flow (PRD §A.9).

### Phase 5 — Android client (weeks 5–7, parallelizable with Phase 4)

Goal: native Android app at parity with iOS.

Same week-by-week shape as iOS. Cross-cutting Android rules:
- Jetpack Compose UI; `androidx.lifecycle.viewmodel.compose` for state; `Hilt` for DI; `Retrofit` + `OkHttp` for networking.
- JWT in `EncryptedSharedPreferences` (or `DataStore` with the crypto wrapper).
- No network calls on the main thread. Specifically, the share intent dispatcher must not `runBlocking` — it'll ANR under bad network and lose the share-sheet budget (CLAUDE.md gotchas).
- `NotificationCompat.BigTextStyle` + `setLargeIcon` planned for Phase 6's rich notifications.

### Phase 6 — Share-sheet integration end-to-end (week 8)

Goal: the marquee flow works on both platforms, within the 3s p50 budget.

Deliverables:
- iOS Share Extension target. Memory ceiling ~120MB; keep it minimal — parse the URL, POST to `/recommend` with the JWT from the shared Keychain group, exit. No heavy frameworks linked.
- iOS Notification Service Extension target for rich notifications with the product image attachment.
- Android `Activity` with `ACTION_SEND` + `text/plain` filter. Same minimal pattern.
- Local fallback notification scheduled if the backend response hasn't arrived in 6s (PRD §13). In-app inbox always reflects latest state, so users have a recovery path even if the push is missed.
- Push dispatch on the backend: APNs (token auth) and FCM. Push payload conforms to PRD §7.4 (size + confidence in title, brand + product in subtitle, one-line summary in body, two deep-link actions).
- End-to-end latency tests in CI: a synthetic share flow against a staging backend with fixture brands, asserting p50 < 3s.

Exit criterion: a real device on either platform, sharing a real product URL from Mobile Safari / Chrome to Sizeify, receives a rich notification with the right size in under 3 seconds.

### Phase 7 — Outcome tracking and learning loop (week 9)

Goal: the feedback loop that lets us measure correctness lands.

Deliverables:
- 14-day outcome prompt: scheduled local notification on iOS, WorkManager job on Android. Tapping opens an outcome confirmation screen.
- Outcome confirmation flow: correct / incorrect, with optional "add this garment to closet" entry point.
- Backend endpoint `PATCH /recommendations/{id}/outcome` updating `outcome` and `outcome_confirmed_at`.
- Analytics events for: onboarding funnel, share-sheet success vs. failure modes, NLP edit rate per prompt version, confidence calibration. Wired to a single events table; no third-party SDK in v1 to keep the privacy story simple.
- Diagnostic dashboards (PRD §12.2): correctness, latency p50/p95, brand coverage hit rate, NLP extraction edit rate, confidence calibration plot, profile-maturity histogram. Hosted as a small internal-only `/admin/metrics` page (read-only).

Exit criterion: a recommendation made today shows up in the correctness rate within 14 days; a prompt version bump is visible as a clean break in the edit-rate chart.

### Phase 8 — Hardening, testing, beta (weeks 10–11)

Goal: the app is releasable.

Deliverables:
- Load test on `/recommend`: synthetic share flow at expected v1 volume (a single user, ~tens of recommendations/day) and at 10× headroom. Latency budgets hold at p95.
- Chaos drills: scraper module returns 5xx → graceful "couldn't find product" notification. LLM provider returns 5xx → manual-entry fallback. Push provider down → local fallback notification path proven.
- Security pass: JWT rotation tested; Argon2 cost calibrated; rate limiting on `/auth/*`; CORS locked to mobile origins; secrets in environment, not in repo. Snyk / `pip-audit` / `gradle dependencyCheck` clean.
- Privacy review: only feedback + category schema cross the LLM boundary; LLM provider is on a zero-retention or enterprise tier; data export and delete endpoints work end-to-end (verified with synthetic accounts).
- TestFlight + Internal Testing track distributions with the founding user.
- App Store and Play Store metadata, privacy nutrition label / data safety form filled to match the actual data flows.

Exit criterion: founding user can complete onboarding, add 5 garments, and successfully use the share-sheet flow on both real iOS and Android devices in production builds.

### Phase 9 — Validation period (3 months)

Goal: prove the ≥80% correctness target.

Activities:
- Weekly review of correctness, calibration plots, and NLP edit rate.
- Bi-weekly prompt-version refinement triggered by edit-rate spikes; old `fit_signal` rows preserved.
- Bi-weekly scraper-health review and drift fixes.
- Hand-tuned stretch coefficients adjusted only when a clear, repeated outcome pattern justifies the change. (No learning loop in v1 — explicit PRD §6.3 rule.)
- Decision gate at month 3: if correctness ≥ 80% and latency holds, plan v2 (additional categories per PRD §14). Otherwise, root-cause the gap before expanding scope.

---

## 5. Cross-cutting concerns

These are the things every phase must respect.

### Performance budgets are CI gates

Per-step latency budgets from PRD §9.2 are enforced by tests on the share-sheet path. A PR that breaks a budget by more than 10% should fail CI by default; intentional regressions need an explicit owner sign-off.

### Pure cores, side effects at the edges

Fit-profile construction, the matching engine, and the LLM-output validator are pure functions over their inputs. This is the hill to die on for testability. Database access lives in repositories. HTTP lives in clients. Push dispatch lives in a service. The hot path orchestrates them, doesn't reimplement them.

### Versioning everything that drifts

LLM prompts: tagged version, never edited in place. Scraper modules: per-module `version` field on every `brand_product` row (`scraper_version`). Recommendation rows: store `prompt_version`. These are the things that silently change behavior, and they are the things you'll wish you'd versioned the first time something starts going wrong.

### Privacy is a product principle, not a checklist item

PRD §11 defines a tight envelope. The two non-negotiables: (1) only feedback + category schema cross the LLM boundary, and (2) the LLM provider must be zero-retention or enterprise-tier. Everything else (data minimization, GDPR/CCPA flows, photo encryption) is built in from day one because it's cheaper than retrofitting (PRD §11 closing line).

### Observability without invasive analytics

Events table on Postgres, plus structured logs to stdout. No third-party analytics SDK in v1. The diagnostic dashboards are an internal admin page; they don't ship to users. This keeps the privacy nutrition label honest.

### Don't preclude CV-assist (Appendix A)

The `measurements` JSONB on every garment row stores per-dimension `{value, unit, source}` triples where `source ∈ {manual_tape, cv_assisted, imported}`. Photos are a first-class field even though they are optional in v1. Camera permission UX is planned even though it isn't requested in v1. (PRD §A.9.)

---

## 6. Risk register and mitigations

PRD §13 lists the canonical risks. The build plan addresses them as follows:

| Risk | How the plan handles it |
|---|---|
| Scrapers break when retailers change HTML | Per-brand modules, fixture-frozen CI tests, daily live synthetic runs, on-call diff alerts. Phase 2 deliverable. |
| LLM hallucinates fit signals | Mandatory user review of every extraction; edit-rate tracked per prompt version; quick prompt iteration. Phase 3 + Phase 9. |
| Users measure inaccurately | Validation ranges with confirmation prompts; outlier detection; outcome-driven re-measurement suggestions. Phase 4/5 + Phase 7. |
| Cold-start (too few garments) | Onboarding requires 3+ garments; system suppresses high-confidence claims with thin closets; "add more shirts" prompts. Phase 4/5. |
| Stretch mis-estimated | Stretch is a coarse 4-level enum; coefficients accommodate noise; no learning loop without scope approval. Phase 1. |
| Insta/TikTok URL wrappers | Explicit handling in URL resolver; user-prompt fallback when unresolvable. Phase 2. |
| Push notification reliability | Local fallback notification at 6s; in-app inbox always authoritative. Phase 6. |

The two risks the plan adds, not in §13:

- **Founder/single-user dataset bias.** The 80% correctness bar is over a single user's three months. Mitigation: log diagnostic metrics that are *user-independent* (NLP edit rate, scraper coverage, calibration) so we can recognize when the model fits the founder rather than the world.
- **Apple / Google review surprises.** Share extensions and rich notifications occasionally trigger reviewer questions. Mitigation: TestFlight / Internal Testing in Phase 8 catches reviewer-style issues early; privacy disclosures in store metadata exactly match the actual data flow (no surprises in the privacy nutrition label).

---

## 7. Definition of done for v1

v1 is "done" when every item below is true. Anything weaker means we shipped something we can't measure, and the 3-month validation window stops being meaningful.

1. The four-component skeleton is deployed: API in production, scraper worker green, LLM extraction service routed through Anthropic on a zero-retention tier, both mobile apps in production tracks.
2. All ten brands from PRD §7.6 have working scrapers with fixture coverage and three consecutive days of green daily synthetic runs.
3. The share-sheet flow on both platforms hits p50 < 3s, p95 < 5s on a real device against a real product URL.
4. Every recommendation rendered to the user contains all four mandatory components from PRD §5.4 (size, confidence, fit notes, reference garments). Confidence < 60% renders two candidates with trade-offs.
5. Onboarding completion (install → first recommendation) ≥ 50% in TestFlight / Internal Testing cohorts.
6. Outcome tracking is live: 14-day prompt, confirmation flow, correctness rate visible on the admin dashboard.
7. GDPR/CCPA export and delete endpoints work end-to-end and are documented in the privacy policy.
8. The Definition of Done checklist for individual PRs is wired into the repo (lint clean, types clean, tests added or marked, latency budget unaffected on hot-path changes, prompt-version bumped on prompt edits).

---

## 8. What to read next

- `FILE_STRUCTURE.md` — directory layout and per-folder responsibilities for the four components.
- `docs/Sizeify_PRD_v1.docx` — the PRD itself. The Glossary in Appendix B is short; learn it.
- `CLAUDE.md` — the conventions that override defaults. In particular, the v1 scope rules, the privacy rules, and the gotchas.
