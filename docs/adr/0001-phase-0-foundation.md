# ADR 0001: Phase 0 Foundation & Toolchain

- **Status:** Accepted
- **Date:** 2026-05-20
- **Phase:** 0 (Foundation)

## Context

Phase 0 establishes the monorepo skeleton and a reproducible, enforced
developer toolchain across the four components (backend API, iOS, Android,
shared infra). This ADR records the toolchain versions chosen and the
non-obvious decisions made while wiring them, so later phases inherit a fixed,
documented baseline rather than re-litigating these choices.

The exit criterion for Phase 0 — verified end-to-end on a clean clone — is:
`make bootstrap` reaches all-green, `GET /health` returns 200, both mobile apps
build, `alembic current` reports the baseline migration, and all three CI
workflows are green.

## Decision

### Backend (`apps/api`) — Python, managed with uv

| Tool | Version |
| --- | --- |
| Python | ≥ 3.12 (CI pins 3.12) |
| uv | 0.11.14 |
| FastAPI | 0.136.1 |
| uvicorn | 0.46.0 |
| pydantic | 2.13.4 |
| SQLAlchemy | 2.0.49 |
| asyncpg | 0.31.0 |
| Alembic | 1.18.4 (async env template) |
| arq | 0.28.0 |
| anthropic | 0.102.0 |
| ruff | 0.15.12 (lint + format, line length 100) |
| mypy | 2.1.0 (strict, on `src/api`) |
| pytest | 9.0.3 (+ pytest-asyncio, httpx 0.28.1) |

### iOS (`apps/ios`) — native SwiftUI

| Tool | Version |
| --- | --- |
| Xcode (CI) | 15.4 (iPhone 15 simulator) |
| Swift language mode | 5 |
| Deployment target | iOS 17 |
| swift-format (Apple) | 6.2.3 (`.swift-format`) |
| SwiftLint | 0.63.2 |

### Android (`apps/android`) — native Jetpack Compose

| Tool | Version |
| --- | --- |
| Gradle | 8.11.1 (wrapper) |
| Android Gradle Plugin | 8.7.2 |
| Kotlin | 2.0.21 |
| KSP | 2.0.21-1.0.27 |
| Hilt | 2.52 |
| Compose BOM | 2024.10.01 |
| JDK (Gradle daemon) | 17 |
| SDKs | minSdk 26 · targetSdk 34 · compileSdk 34 |
| ktlint-gradle | 12.1.2 |
| detekt | 1.23.7 |

### Infrastructure & CI

| Tool | Version / Choice |
| --- | --- |
| PostgreSQL | 16 (Docker) |
| Redis | 7 (Docker) |
| Container runtime (local) | Colima + docker-compose |
| CI | GitHub Actions |
| Runners | `ubuntu-latest` (backend, android) · `macos-14` (iOS) |
| Actions | setup-uv@v4 · setup-java@v4 (temurin 17) · gradle/actions/setup-gradle@v4 · maxim-lobanov/setup-xcode@v1 · dorny/paths-filter@v3 |
| pre-commit | 4.6.0 |

## Notable decisions & deviations

1. **Android Gradle daemon on JDK 17.** The dev machine's default JDK is 25,
   which Gradle 8.x cannot launch on. We require Gradle 8.x (per spec), so the
   daemon toolchain is pinned to JDK 17 (`gradle/gradle-daemon-jvm.properties`)
   — also the canonical JDK for AGP 8.x. App bytecode targets 17.

2. **Apple `swift-format`, not the third-party `swiftformat`.** The project
   uses Apple's `swift format` (config `.swift-format`), which ships with the
   Swift toolchain — no extra install. SwiftLint is separate, via brew.

3. **`api` import resolution is explicit, not via the editable install.** uv's
   editable `.pth` is not reliably honored in this venv, causing intermittent
   `ModuleNotFoundError: No module named 'api'`. We resolve `src` directly:
   pytest `pythonpath = ["src"]` and Alembic `prepend_sys_path = src`.

4. **CI uses an always-running "gate" job pattern.** Each workflow runs a cheap
   path-detection job; the expensive build/test job runs only when its
   component changed; a `gate` job always runs and is the required status check.
   This avoids the monorepo deadlock where a path-filtered required check never
   reports on unrelated PRs. Required checks on `main`: `backend gate`,
   `ios gate`, `android gate` (strict / branch-up-to-date).

5. **Repository is private, on GitHub Pro.** Branch protection / rulesets with
   required status checks are gated behind a paid tier (Pro/Team/Enterprise) on
   private repos; on the free tier they work only on public repos. The repo was
   briefly public during early Phase 0 to get enforced gates for free, then
   moved to private under GitHub Pro once the proprietary code (scraper modules,
   stretch coefficients, prompt templates) warranted it — Pro preserves the same
   enforced required checks on `main`. Trade-off: private repos meter GitHub
   Actions minutes (Pro: 3,000/mo; macOS runners bill at 10×), whereas public
   repos get unlimited standard-runner minutes. The gate pattern (item 4) keeps
   the expensive macOS iOS build off PRs that don't touch `apps/ios/**`, so the
   quota is ample for solo development.

6. **iPhone 15 in CI, iPhone 16 locally.** macos-14 + Xcode 15.4 provides the
   iPhone 15 simulator (matching spec). The local dev machine runs Xcode 26.2,
   whose default simulator set has no iPhone 15, so local builds verify against
   iPhone 16. Both are simulator-only, ad-hoc signed.

## Consequences

- The versions above are the Phase 0 baseline; bumping any of them is a
  deliberate change, ideally noted in a follow-up ADR when it has
  cross-component impact.
- New contributors need these host tools present before `make bootstrap`: uv,
  a running Docker daemon, Xcode CLI tools, JDK 17, and the Android SDK.
  `scripts/bootstrap.sh` checks for all of them and fails fast with install
  hints. The Android SDK check resolves the SDK from `ANDROID_HOME` /
  `ANDROID_SDK_ROOT` or `apps/android/local.properties` (`sdk.dir=`), since
  `local.properties` is gitignored and absent on a fresh clone.
- Branch protection is enforced on `main`: the three gate checks (`backend
  gate`, `ios gate`, `android gate`) are required and strict (branch must be
  up to date); force-pushes and deletions are blocked. `enforce_admins` is
  off so the solo maintainer is not locked out.
- **Tech debt:** several GitHub Actions still run on Node 20 (deprecation
  deadline mid-2026) — a version bump pass is queued for a later phase.
