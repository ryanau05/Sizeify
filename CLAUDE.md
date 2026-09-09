# Sizeify

Mobile app that recommends what size to buy in any clothing brand based on garments the user already owns. v1 scope: men's button-down shirts, 10 partner brands, single user.

**Authoritative spec:** `docs/Sizeify_PRD_v1.docx`. When in doubt about requirements, read the PRD before guessing. The Glossary (Appendix B) defines domain terms — use them consistently.

**Project plan and current status:** `docs/PROJECT_PLAN.md` is the phased build plan (Phase 0 → 9). `docs/PHASE_1_TICKETS.md` breaks the active phase into tickets. Read these before proposing work — they tell you what's done, what's in flight, and what's deferred. The PRD says *what* to build; the plan says *where we are* in building it.

**Current status (as of 2026-09-09):** Phase 0 (Foundation) is complete and the repo is **public** with branch protection on `main` (required status checks: backend/ios/android gates). Phase 1 (Backend core) is **in progress**: schema, repositories, Pydantic schemas, auth (Argon2 + JWT), and migrations `0001`/`0002` are landed, and the domain core (`stretch`, `fit_profile`, `matching`, `recommendation`) is now landed with unit tests. Closet and recommendation endpoints are **not built yet** — see `docs/PHASE_1_TICKETS.md`. The capstone demo track is finished and its branches (`demo/capstone`, `docs/demo-status`) have been deleted locally and on `origin`; the domain-core work it produced was cherry-picked onto `main` and everything else (the `DEMO_MODE` app and throwaway web client) was discarded. Their full history is preserved in the annotated tags `archive/demo-capstone` and `archive/docs-demo-status` — recover with `git checkout -b <name> archive/demo-capstone` rather than assuming the code is gone. Keep this paragraph current — if the date here is more than a week old, treat the status as suspect and re-read `docs/PROJECT_PLAN.md` before acting on it.

## Architecture

Four independently deployable components:

- **Mobile client** (iOS + Android) — closet management, measurement input, share-extension entry point, recommendation display.
- **Backend API** — stateless; fit-profile construction, matching, recommendation generation.
- **Scraper service** — per-brand modules behind a uniform interface; daily refresh plus on-demand for unknown URLs.
- **LLM extraction service** — thin proxy for converting free-text fit feedback into structured signals.

**Stack:**
- Backend: Python 3.12+ with FastAPI, SQLAlchemy + Alembic, asyncpg. Managed with `uv`.
- Mobile: native iOS (Swift/SwiftUI) and native Android (Kotlin/Jetpack Compose). Native chosen because share extensions, share intents, and rich notifications — the entire discovery flow — require platform-specific code regardless of framework.
- Database: PostgreSQL 16. Redis for caching (fit profiles, brand_product lookups) once measured to be needed.
- LLM: Anthropic API via the official SDK, behind the extraction service proxy.

See PRD §8 (data model) and §9 (architecture) for details.

## Commands

### Backend (`apps/api`)

- Bootstrap: `uv sync`
- Run locally: `uv run fastapi dev src/api/main.py`
- Run all tests: `uv run pytest`
- Run a single test: `uv run pytest tests/path/to/test_file.py::test_name`
- Lint + format: `uv run ruff check --fix && uv run ruff format`
- Type check: `uv run mypy src`
- Create migration: `uv run alembic revision --autogenerate -m "description"`
- Apply migrations: `uv run alembic upgrade head`
- Run a scraper against a real URL: `uv run python -m scrapers.cli --brand <brand> --url <URL>`

### iOS (`apps/ios`)

- Open in Xcode: `open Sizeify.xcworkspace`
- Build from CLI: `xcodebuild -workspace Sizeify.xcworkspace -scheme Sizeify -destination 'platform=iOS Simulator,name=iPhone 16' build`
- Run tests: `xcodebuild test -workspace Sizeify.xcworkspace -scheme Sizeify -destination 'platform=iOS Simulator,name=iPhone 16'`
- Format: `swift format -i -r Sources/`
- Lint: `swiftlint`

### Android (`apps/android`)

- Build debug: `./gradlew assembleDebug`
- Run unit tests: `./gradlew test`
- Run instrumented tests: `./gradlew connectedAndroidTest`
- Format + lint: `./gradlew ktlintFormat ktlintCheck detekt`

### Local infrastructure

- Start Postgres + Redis: `docker compose up -d`
- Stop: `docker compose down`
- Reset DB (destructive): `docker compose down -v && docker compose up -d && (cd apps/api && uv run alembic upgrade head)`
- Tail backend logs: `docker compose logs -f api`

> Always run lint/format before committing. Wire these into a pre-commit hook (`pre-commit install`) rather than relying on Claude to remember.

## v1 scope rules

The PRD has an unusually explicit non-goals section. Respect it — scope creep is the main failure mode for this product.

- **IMPORTANT:** v1 supports **only** men's button-down shirts. Do not add measurement schemas, fit dimensions, or UI for other categories without explicit approval.
- **IMPORTANT:** v1 supports **only** the 10 brands listed in PRD §7.6. Adding a brand requires a new scraper module, validation against real products, and approval.
- Out of scope: style/aesthetic recommendations, virtual try-on, social features, affiliate links, feed scraping, women's/plus/petite ranges, multi-user closets. See PRD §3.2.
- **No CV-based measurement in v1** — guided manual measurement only. However, the data model and UI **must not preclude** the CV-assist flow described in Appendix A. Read §A.9 before making structural choices around photos, measurement records, and camera UX.

## Performance & accuracy budgets

These are PRD requirements, not aspirations. Flag any change that risks breaching them.

- Share-sheet → notification: **p50 < 3s, p95 < 5s**. Per-step budgets in PRD §9.2 (resolve 200ms · scraper 1.5s · profile build 100ms · matching 300ms · push 500ms).
- Recommendation correctness target: **≥80%** over 3-month live use.
- Onboarding: first garment entered in **<4 minutes** via guided flow.
- Every recommendation must include all four mandatory components (PRD §5.4): size, confidence, fit notes, reference garments.

## Domain conventions

- Use the exact terms from the PRD glossary: *closet, fit profile, fit signal, use case, stretch level, reference garment*. Do not invent synonyms.
- Measurements are stored in **cm** internally regardless of the user's display preference. Convert at the UI boundary only.
- Verdict enum: `too_tight | slightly_tight | preferred | slightly_loose | too_loose`. Sleeve-length feedback adds `slightly_short` / `too_short` analogues per PRD §6.1.
- Stretch levels: `none | slight | moderate | high`. Coarse on purpose; do not propose finer granularity.
- Every `fit_signal` row stores its `source` (`nlp_extracted | user_edited | user_added`) and the raw feedback excerpt. Do not strip these — they are required for prompt-quality debugging.
- Every `recommendation` row stores the LLM prompt version that produced its signals. Required for detecting extraction drift across prompt updates.
- Confidence < 60% → display **two** candidate sizes with trade-offs, not one (PRD §5.4).

## Privacy & data rules

- **IMPORTANT:** do not send anything to the LLM provider beyond the free-text feedback and category schema needed for extraction. No email, no full closet dump, no user identifiers.
- **IMPORTANT:** the LLM provider must be zero-retention or enterprise-tier. Document in the privacy policy.
- Data minimization is a product principle (PRD §11). When adding a feature, ask whether new data collection is actually required.
- GDPR/CCPA flows (export, delete, consent) are required from day one — cheaper now than retrofitted.

## Workflow rules

- For changes touching multiple components (mobile + backend + scrapers), write a short plan and wait for approval before editing.
- Scraper modules share a uniform interface (PRD §9.3). New brand scrapers must conform — do not introduce per-brand interface variants.
- LLM prompts live in version control as tagged templates. Changing a prompt means bumping its version, not editing in place — old `fit_signal` rows reference old versions.
- When adding a measurement dimension or use case, update **all four** of: (a) `garment_category.measurement_schema`, (b) `dimension_weights`, (c) the NLP extraction prompt, (d) the matching engine. Missing any of these silently degrades recommendations.

## Gotchas

- Share extensions cannot run long work — they ping the backend and exit. Results return via push notification (PRD §9.5). Don't try to do anything synchronously in the extension.
- Never assume "M means M" across brands — always go through the brand's size chart.
- Stretch coefficients are hand-tuned in v1 (PRD §6.3). Do not add a learning loop without explicit scope approval.
- URL resolution must handle Instagram/TikTok wrapper URLs explicitly. Hard 2s timeout — fail fast rather than hang the share flow.
- Local fallback notification is required if the backend response hasn't arrived in 6s (PRD §13).

### Backend (Python / FastAPI)

- Use `async def` route handlers and `asyncpg` throughout. A single sync DB call on the share-sheet path will eat the latency budget.
- All request/response bodies are Pydantic models. Don't return raw dicts from handlers — the schema is the contract.
- Background tasks (scraper kickoff, push dispatch) should not run in FastAPI's `BackgroundTasks` — those die with the request worker. Use a proper queue (start with a lightweight option like `arq` on Redis; defer Celery).
- Database access goes through the repository layer; no raw SQLAlchemy sessions in route handlers. Keeps tests sane.

### iOS

- Share Extension is a separate target with a strict memory ceiling (~120MB). Keep its code minimal: parse the URL, POST to backend with the user's JWT from the shared Keychain group, exit. No heavy frameworks linked.
- Use App Groups to share the JWT and minimal user state between the main app and the Share Extension.
- Rich push notifications require a Notification Service Extension target for any media attachments (e.g., product image in the notification).
- JWT lives in Keychain with `kSecAttrAccessibleAfterFirstUnlock` so the share extension can read it when the device is locked-but-unlocked-since-boot.

### Android

- Share Intent handler is an `Activity` with `ACTION_SEND` + `text/plain` filter. Same pattern as iOS: receive URL, fire request, finish.
- Rich notifications use `NotificationCompat.BigTextStyle` plus `setLargeIcon` for product imagery.
- JWT lives in EncryptedSharedPreferences (or DataStore with the crypto wrapper).
- Don't put network calls on the main thread — share intent dispatchers using `runBlocking` will ANR under bad network.

### Scrapers

- One module per brand, all conforming to the interface in `scrapers/base.py`. No per-brand interface variants.
- Each scraper has a fixture file with at least 5 known products and their expected size charts. Daily synthetic test runs against these to detect HTML drift early.
- Prefer Shopify/affiliate APIs where available (PRD §9.3). Fall back to HTML scraping only when needed.
- If a brand requires JS rendering, isolate it to that brand's module using Playwright — don't drag a headless browser into modules that don't need it.

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available gstack skills:

- `/office-hours`
- `/plan-ceo-review`
- `/plan-eng-review`
- `/plan-design-review`
- `/design-consultation`
- `/design-shotgun`
- `/design-html`
- `/review`
- `/ship`
- `/land-and-deploy`
- `/canary`
- `/benchmark`
- `/browse`
- `/connect-chrome`
- `/qa`
- `/qa-only`
- `/design-review`
- `/setup-browser-cookies`
- `/setup-deploy`
- `/setup-gbrain`
- `/retro`
- `/investigate`
- `/document-release`
- `/document-generate`
- `/codex`
- `/cso`
- `/autoplan`
- `/plan-devex-review`
- `/devex-review`
- `/careful`
- `/freeze`
- `/guard`
- `/unfreeze`
- `/gstack-upgrade`
- `/learn`

Teammates: gstack is not vendored in this repo — install it locally with
`git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack && cd ~/.claude/skills/gstack && ./setup`
