# Sizify v1 — File Structure

This is the canonical repo layout. It mirrors the four-component architecture (mobile clients, backend API, scrapers, LLM extraction) plus shared infrastructure and docs. Each top-level folder owns exactly one of those concerns; nothing crosses without an explicit interface.

The tree below is annotated. Read the annotation under each folder as the contract: that's what lives there, that's what doesn't.

---

## Top-level layout

```
sizify/
├── apps/                        # Independently deployable units
│   ├── api/                     # FastAPI backend (Python 3.12, uv)
│   ├── ios/                     # Native iOS app (SwiftUI)
│   └── android/                 # Native Android app (Compose)
│
├── infra/                       # Local + cloud infrastructure
│   ├── docker-compose.yml       # Postgres 16 + Redis for local dev
│   ├── terraform/               # IaC for staging + prod (deferred until needed)
│   └── k8s/                     # Manifests for API + worker (deferred)
│
├── docs/                        # Specs, plans, and decision records
│   ├── Sizify_PRD_v1.docx # Authoritative product spec
│   ├── PROJECT_PLAN.md          # Phased implementation plan
│   ├── FILE_STRUCTURE.md        # This file
│   ├── adr/                     # Architecture Decision Records (one .md per decision)
│   └── runbooks/                # On-call and operational guides
│
├── scripts/                     # Repo-wide developer scripts
│   ├── bootstrap.sh             # Idempotent dev-env setup
│   ├── lint-all.sh              # Runs all linters across the monorepo
│   └── e2e/                     # End-to-end share-sheet latency harness
│
├── .github/
│   └── workflows/               # CI: backend, ios, android, scrapers-daily
│
├── .pre-commit-config.yaml      # ruff, ruff-format, ktlint, swiftformat hooks
├── CLAUDE.md                    # Repo-wide conventions (already exists)
├── README.md                    # 60-second project overview + bootstrap link
└── Makefile                     # Targets: bootstrap, dev, lint, test, db-reset
```

The four `apps/*` directories are deliberately at the same depth and named the same way (`api`, `ios`, `android`). Each one owns its own dependency manifest and CI workflow — no cross-imports between them. Sharing is via the wire, not via shared code.

---

## `apps/api/` — Backend (FastAPI + asyncpg + SQLAlchemy)

```
apps/api/
├── pyproject.toml               # uv-managed; declares fastapi, sqlalchemy, asyncpg, alembic, anthropic, arq
├── uv.lock
├── alembic.ini
├── .env.example                 # All required env vars; copy to .env locally
├── README.md                    # How to run, test, migrate, scrape
│
├── alembic/
│   ├── env.py
│   └── versions/                # Migrations; named with description per CLAUDE.md
│
├── src/
│   └── api/
│       ├── __init__.py
│       ├── main.py              # FastAPI app factory, middleware, route includes
│       ├── config.py            # Pydantic Settings: DB URL, Redis, Anthropic key, JWT secret
│       ├── deps.py              # FastAPI dependencies: db session, current user
│       ├── logging.py           # Structured JSON logging config
│       │
│       ├── models/              # SQLAlchemy ORM models, one file per entity
│       │   ├── __init__.py
│       │   ├── user.py
│       │   ├── garment_category.py
│       │   ├── owned_garment.py
│       │   ├── fit_signal.py
│       │   ├── brand_product.py
│       │   ├── recommendation.py
│       │   └── event.py         # Diagnostic events for §12.2 metrics
│       │
│       ├── schemas/             # Pydantic request/response models. Route handlers
│       │   ├── __init__.py     # never return raw dicts — schema is the contract.
│       │   ├── auth.py
│       │   ├── closet.py
│       │   ├── recommendation.py
│       │   └── outcome.py
│       │
│       ├── repositories/        # All DB access. Route handlers go through these.
│       │   ├── __init__.py
│       │   ├── base.py          # AsyncSession boilerplate; transaction helpers
│       │   ├── users.py
│       │   ├── garments.py
│       │   ├── fit_signals.py
│       │   ├── brand_products.py
│       │   └── recommendations.py
│       │
│       ├── routes/              # Thin handlers. Orchestrate services + repositories.
│       │   ├── __init__.py
│       │   ├── health.py
│       │   ├── auth.py          # /auth/signup, /login, /refresh
│       │   ├── closet.py        # /closet/garments, /fit-feedback, /fit-profile
│       │   ├── recommend.py     # /recommend  ← the share-sheet hot path
│       │   ├── outcomes.py      # /recommendations/{id}/outcome
│       │   ├── me.py            # /me/export, DELETE /me  (GDPR/CCPA)
│       │   └── admin.py         # /admin/metrics  (internal-only, gated)
│       │
│       ├── domain/              # Pure domain logic. No I/O. Heavily unit-tested.
│       │   ├── __init__.py
│       │   ├── fit_profile.py   # PRD §6.2 Bayesian construction
│       │   ├── matching.py      # PRD §6.4 weighted-distance + confidence
│       │   ├── stretch.py       # PRD §6.3 hand-tuned coefficients (data + apply fn)
│       │   ├── confidence.py    # Calibration helpers, two-candidate split (§5.4)
│       │   └── units.py         # cm-only invariant; conversion utilities
│       │
│       ├── services/            # Effectful orchestration: HTTP, queue, push.
│       │   ├── __init__.py
│       │   ├── url_resolver.py  # Redirects + Insta/TikTok wrappers, 2s hard timeout
│       │   ├── recommend.py     # Orchestrates the §9.2 hot path
│       │   ├── push/
│       │   │   ├── __init__.py
│       │   │   ├── apns.py
│       │   │   ├── fcm.py
│       │   │   └── dispatcher.py
│       │   ├── outcome_scheduler.py  # 14-day outcome prompt scheduling
│       │   └── analytics.py     # Writes to events table; no third-party SDK
│       │
│       ├── workers/             # arq tasks. Bound to a separate process from the API.
│       │   ├── __init__.py
│       │   ├── settings.py      # arq WorkerSettings: queue config, concurrency
│       │   ├── scrape_on_demand.py
│       │   └── scrape_daily.py  # Cron-triggered refresh of known products
│       │
│       └── auth/
│           ├── __init__.py
│           ├── password.py      # Argon2 hashing
│           └── jwt.py           # Issuance, rotation, verification
│
├── llm/                         # LLM extraction service. Importable as `llm`.
│   ├── __init__.py              # Public: extract_fit_signals(...)
│   ├── client.py                # Anthropic SDK wrapper: retries, timeouts
│   ├── extractor.py             # Prompt construction + schema validation
│   ├── schema.py                # Pydantic ExtractionResult, FitSignalSchema
│   └── prompts/                 # Versioned templates. NEVER edit in place.
│       ├── fit_extraction/
│       │   ├── v1.md
│       │   └── README.md        # How to bump a version
│       └── README.md
│
├── scrapers/                    # Per-brand scraper modules + uniform interface.
│   ├── __init__.py
│   ├── base.py                  # BrandScraper ABC, BrandProduct dataclass, errors
│   ├── registry.py              # hostname → module dispatcher
│   ├── cli.py                   # `python -m scrapers.cli --brand X --url Y`
│   ├── http.py                  # Shared respectful client (UA, rate limiter)
│   ├── playwright_pool.py       # Used ONLY by modules that require JS rendering
│   ├── uniqlo/
│   │   ├── __init__.py
│   │   ├── scraper.py
│   │   └── fixtures/            # ≥5 known products + expected size charts
│   ├── jcrew/
│   ├── bonobos/
│   ├── everlane/
│   ├── banana_republic/
│   ├── brooks_brothers/
│   ├── charles_tyrwhitt/
│   ├── mr_porter/
│   ├── spier_mackay/
│   └── proper_cloth/
│
└── tests/
    ├── conftest.py              # DB fixtures, async test client, factory_boy factories
    ├── unit/
    │   ├── domain/
    │   │   ├── test_fit_profile.py
    │   │   ├── test_matching.py
    │   │   └── test_stretch.py
    │   ├── llm/
    │   │   └── test_extractor.py
    │   └── scrapers/
    │       └── test_<brand>.py  # One per brand, against frozen fixtures
    ├── integration/
    │   ├── test_recommend_flow.py
    │   ├── test_closet_crud.py
    │   └── test_outcomes.py
    ├── latency/
    │   └── test_share_sheet_budget.py  # CI gate on PRD §9.2 budgets
    └── fixtures/
        └── closets/             # Hand-crafted closets for matching tests
```

A few things to call out:

- `domain/` is pure functions only. If you find yourself reaching for `httpx` or `select()` in a domain module, you're in the wrong file.
- `services/` is where side effects live. The hot-path orchestration is `services/recommend.py`; route handlers stay thin.
- `repositories/` is the only place SQLAlchemy sessions are touched. Route handlers must not import `AsyncSession` directly. CLAUDE.md is explicit about this and it pays off in test simplicity.
- `scrapers/` is a sibling of `api/` because scrapers are conceptually a separate component. They share the database but not the request/response stack.
- `llm/` is a sibling for the same reason. The folder is small but it's the place to add prompt versions, evals, and provider switching without touching API routes.

---

## `apps/ios/` — Native iOS (SwiftUI)

```
apps/ios/
├── Sizify.xcworkspace
├── Sizify.xcodeproj
├── Package.swift                # SwiftPM dependencies (Alamofire or URLSession-only,
│                                #   KeychainAccess, swift-dependencies, etc.)
├── README.md
│
├── Sizify/                # Main app target
│   ├── SizifyApp.swift
│   ├── Info.plist
│   ├── Assets.xcassets
│   ├── Resources/
│   │   └── MeasurementGuides/   # PRD §10.1 photographic measurement guides
│   │
│   ├── Core/
│   │   ├── Networking/
│   │   │   ├── APIClient.swift  # Generic JSON client, JWT injection
│   │   │   ├── Endpoints.swift  # Typed endpoint declarations
│   │   │   └── DTO/             # Mirrors apps/api schemas exactly
│   │   ├── Auth/
│   │   │   ├── KeychainStore.swift  # AccessibleAfterFirstUnlock; App Group enabled
│   │   │   └── AuthService.swift
│   │   ├── Storage/             # SwiftData / Core Data for offline closet cache
│   │   ├── Push/
│   │   │   ├── PushHandler.swift
│   │   │   └── DeepLinkRouter.swift
│   │   └── Units/               # cm-only internal model; UI-only conversion
│   │
│   ├── Features/                # One folder per top-level surface (PRD §10.2)
│   │   ├── Onboarding/          # PRD §10.1 seven-step flow
│   │   ├── Closet/              # List + detail + add-garment flows
│   │   ├── Measurement/         # Guided per-dimension input with validation
│   │   ├── FitFeedback/         # Free text → review chips (§6.1)
│   │   ├── Recommendations/     # List + detail (PRD §10.3 layout)
│   │   ├── Outcomes/            # 14-day prompt + confirmation
│   │   └── Profile/             # Stated prefs, fit profile viz, settings
│   │
│   ├── DesignSystem/
│   │   ├── Colors.swift
│   │   ├── Typography.swift
│   │   ├── Components/          # Reusable views: ConfidenceBadge, ReferenceCard, …
│   │   └── Haptics.swift
│   │
│   └── Utilities/
│       ├── Logger.swift
│       └── FeatureFlags.swift
│
├── SizifyShareExtension/  # Extension target (Phase 6)
│   ├── ShareViewController.swift  # Minimal: read URL, POST /recommend, exit
│   ├── Info.plist               # NSExtension config + App Group entitlement
│   └── SharedSecrets.swift      # Reads JWT from shared Keychain group
│
├── SizifyNotificationServiceExtension/   # Rich notifications target
│   ├── NotificationService.swift  # Downloads product image, attaches to payload
│   └── Info.plist
│
├── SizifyTests/           # Unit tests (XCTest)
│   ├── NetworkingTests/
│   ├── UnitsTests/
│   └── DTOMappingTests/
│
└── SizifyUITests/         # Onboarding happy path, share-flow integration test
```

iOS-specific reminders:

- **App Group entitlement** is added to all three targets so the Share Extension can read JWT from the shared Keychain.
- **Camera permission** is requested lazily (Profile → "Use camera to assist measurement") even though v1 measurement is manual. Don't preclude PRD Appendix A.
- The Share Extension target keeps its dependency footprint small — it has a hard memory ceiling. No SwiftUI, no Alamofire; raw URLSession is fine here.

---

## `apps/android/` — Native Android (Compose)

```
apps/android/
├── settings.gradle.kts
├── build.gradle.kts             # Root build script: ktlint, detekt, plugins
├── gradle/
│   └── libs.versions.toml       # Version catalog (Compose, Retrofit, Hilt, kotlinx)
├── README.md
│
├── app/                         # Main app module
│   ├── build.gradle.kts
│   ├── src/main/
│   │   ├── AndroidManifest.xml
│   │   ├── kotlin/com/sizify/app/
│   │   │   ├── SizifyApp.kt   # Application class; Hilt entry point
│   │   │   ├── MainActivity.kt
│   │   │   ├── ShareReceiverActivity.kt   # ACTION_SEND text/plain handler
│   │   │   │
│   │   │   ├── core/
│   │   │   │   ├── network/        # Retrofit + OkHttp; JWT interceptor
│   │   │   │   ├── auth/           # EncryptedSharedPreferences wrapper
│   │   │   │   ├── storage/        # Room for offline closet cache
│   │   │   │   ├── push/           # FCM service + deep-link router
│   │   │   │   ├── units/          # cm-only invariant; UI conversions
│   │   │   │   └── di/             # Hilt modules
│   │   │   │
│   │   │   ├── features/
│   │   │   │   ├── onboarding/
│   │   │   │   ├── closet/
│   │   │   │   ├── measurement/
│   │   │   │   ├── fitfeedback/
│   │   │   │   ├── recommendations/
│   │   │   │   ├── outcomes/
│   │   │   │   └── profile/
│   │   │   │
│   │   │   ├── designsystem/
│   │   │   │   ├── Theme.kt
│   │   │   │   ├── Colors.kt
│   │   │   │   ├── Typography.kt
│   │   │   │   └── components/     # ConfidenceBadge, ReferenceCard, …
│   │   │   │
│   │   │   └── util/
│   │   │       ├── Logger.kt
│   │   │       └── FeatureFlags.kt
│   │   │
│   │   └── res/
│   │       ├── drawable/           # Measurement-guide illustrations
│   │       ├── values/
│   │       └── mipmap-*/           # Launcher icons
│   │
│   ├── src/test/                   # JVM unit tests
│   │   └── kotlin/com/sizify/app/
│   │       ├── core/
│   │       └── features/
│   │
│   └── src/androidTest/            # Instrumented tests
│       └── kotlin/com/sizify/app/
│           └── share/              # ShareReceiverActivity flow test
│
└── notification-service/           # FCM background work, separate module
    └── src/main/kotlin/...
```

Android-specific reminders:

- `ShareReceiverActivity` is the equivalent of the iOS Share Extension. Same minimal pattern: read URL, fire request, finish. No `runBlocking` on the main thread.
- JWT lives in `EncryptedSharedPreferences`. If we ever need cross-process sharing, switch to `DataStore` with the crypto wrapper.
- Rich notifications use `NotificationCompat.BigTextStyle` + `setLargeIcon` for the product image.

---

## `infra/`

```
infra/
├── docker-compose.yml           # Postgres 16, Redis, optional Mailhog for dev
├── docker/
│   ├── api.Dockerfile
│   └── worker.Dockerfile
├── terraform/                   # Cloud IaC — empty until cloud deploy is needed
│   ├── modules/
│   └── envs/
│       ├── staging/
│       └── prod/
└── k8s/                         # Manifests for api + arq worker (deferred)
```

Local dev only needs `docker compose up -d` to bring up Postgres + Redis. Terraform and k8s scaffolding stays minimal until the team is ready to deploy beyond local + a single managed environment.

---

## `docs/`

```
docs/
├── Sizify_PRD_v1.docx     # Authoritative spec (source of truth)
├── PROJECT_PLAN.md              # The phased plan
├── FILE_STRUCTURE.md            # This document
│
├── adr/                         # Architecture Decision Records, ADR-style
│   ├── 0001-native-mobile.md
│   ├── 0002-fastapi-asyncpg.md
│   ├── 0003-arq-over-celery.md
│   ├── 0004-llm-prompt-versioning.md
│   ├── 0005-scraper-uniform-interface.md
│   └── README.md                # ADR template + index
│
├── runbooks/
│   ├── share-sheet-incident.md  # When p95 spikes
│   ├── scraper-drift.md         # When daily synthetic run goes red
│   ├── llm-extraction-drift.md  # When edit rate spikes
│   └── push-delivery-failure.md
│
└── data-model/
    ├── schema.dbml              # Source of truth diagram for §8 entities
    └── erd.svg                  # Rendered ERD
```

ADRs are short. The point is to capture *why* a decision was made, not to re-document the code.

---

## `scripts/`

```
scripts/
├── bootstrap.sh                 # Idempotent: installs uv, deps, pre-commit, runs migrations
├── lint-all.sh                  # Runs ruff, mypy, ktlint, detekt, swiftformat in one shot
├── e2e/
│   ├── share_sheet_latency.py   # Synthetic share flow against staging; asserts p50 < 3s
│   └── README.md
└── seed-dev-data.py             # Seeds a demo closet for local UI work
```

---

## `.github/workflows/`

```
.github/workflows/
├── api.yml                      # ruff, mypy, pytest on apps/api changes
├── ios.yml                      # xcodebuild + xcodebuild test on apps/ios changes
├── android.yml                  # ./gradlew test ktlintCheck detekt on apps/android changes
├── scrapers-fixtures.yml        # Runs scraper modules against frozen fixtures
├── scrapers-daily.yml           # Cron: runs scrapers against live URLs, alerts on diff
└── e2e-latency.yml              # Periodic synthetic share-sheet latency check
```

Path filters keep CI fast: a doc-only change shouldn't rebuild the iOS workspace.

---

## Conventions, in one place

These are summarized from `CLAUDE.md` so the file structure makes sense even without that context.

- **Single-package-manager-per-app.** Backend uses `uv`. iOS uses SwiftPM. Android uses Gradle with the version catalog. No npm in the backend, no CocoaPods on iOS.
- **Migrations are checked in.** Every schema change is a numbered migration in `alembic/versions/`. The data model in PRD §8 is the source of truth; the migrations are the implementation.
- **No raw SQLAlchemy in routes.** All DB access goes through `apps/api/src/api/repositories/`.
- **No raw dicts in routes.** All request and response bodies are Pydantic models in `schemas/`.
- **Domain is pure.** `apps/api/src/api/domain/` has no I/O. The matching engine is testable in isolation.
- **Prompts are versioned in tree.** `apps/api/llm/prompts/<purpose>/v<N>.md`. Bump version, never edit in place.
- **Scrapers conform.** Every brand module subclasses `scrapers.base.BrandScraper`. Per-brand interface variants are forbidden.
- **Measurements are cm internally.** Display preference is a UI concern. Conversion happens at the boundary.
- **JWT in Keychain (iOS) / EncryptedSharedPreferences (Android).** App Group / FileProvider entitlements set up so share-sheet entry points can read it.

When in doubt, the PRD wins. When the PRD is silent, CLAUDE.md wins. When both are silent, file an ADR.
