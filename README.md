# Sizeify

Mobile app that recommends what size to buy in any clothing brand based on garments the user already owns. v1 covers men's button-down shirts across 10 partner brands.

## What it does

Sizing is inconsistent across brands — an "M" in one label is an "L" in another, so online clothing purchases are a guessing game. Sizeify removes the guess. A user measures a few garments they already own and rates how each one fits. From that, Sizeify builds a personal **fit profile** and matches it against any partner brand's published size chart, accounting for fabric stretch. The intended flow is frictionless: share a product link from any shopping app and get back a size recommendation — with a confidence score, fit notes, and the reference garments the call was based on — delivered via push notification.

## Project status — in active development

**This is an in-progress project, not a finished product.** It is being built in phases (see [PROJECT_PLAN.md](docs/PROJECT_PLAN.md)), and the current snapshot is a backend foundation plus the domain core. The architecture, data model, and product spec are fully designed; the end-to-end recommendation flow is not yet wired up to the API surface.

| Area | Status |
|---|---|
| Product spec, data model, phased plan | Complete (see `docs/`) |
| Backend foundation — DB schema, repositories, Pydantic schemas, auth (Argon2 + JWT), migrations | Implemented and tested |
| Domain core — fit-profile construction, matching engine, recommendation | Implemented and unit-tested |
| Closet & recommendation API endpoints | Not yet built |
| Scrapers, LLM extraction service | Planned (Phases 2–3) |
| iOS & Android clients | Planned (Phases 4–5) |

What's runnable today is the backend data layer and the domain core, with their test suites. The repo is shared at this stage to show the design thinking, architecture, and engineering foundation rather than a working app.

The repo is a monorepo with four independently deployable components:

- `apps/api/` — FastAPI backend (Python 3.12, `uv`)
- `apps/ios/` — Native iOS app (SwiftUI)
- `apps/android/` — Native Android app (Compose)
- `apps/api/scrapers/` and `apps/api/llm/` — Scraper modules and LLM extraction service, deployed alongside the API

Shared concerns live in `infra/` (Docker, Terraform, k8s), `scripts/` (developer tooling), `docs/` (PRD, plan, ADRs, runbooks), and `.github/workflows/` (CI).

## Getting started

Run the idempotent bootstrap script:

```
./scripts/bootstrap.sh
```

It installs `uv`, syncs backend deps, sets up pre-commit hooks, and runs database migrations against the local Postgres + Redis stack from `infra/docker-compose.yml`.

After bootstrap, common tasks are wired into the [Makefile](Makefile): `make dev`, `make lint`, `make test`, `make db-reset`.

## Pre-commit hooks

Lint and format hooks live in [.pre-commit-config.yaml](.pre-commit-config.yaml): ruff + ruff-format on `apps/api/`, ktlint on `apps/android/`, swift-format on `apps/ios/`, plus `end-of-file-fixer` and `trailing-whitespace` repo-wide.

Required host tools (install once):

```bash
brew install pre-commit ktlint swiftlint  # ruff comes in via the pre-commit env
# swift-format is bundled with the Swift toolchain shipped in Xcode 26 — no install
```

Then wire the git hook:

```bash
pre-commit install        # installs the hook into .git/hooks/pre-commit
pre-commit run --all-files
```

The `--all-files` run should exit 0 immediately after `pre-commit install` on a fresh clone.

## Documentation

- [Sizeify_PRD_v1.docx](docs/Sizeify_PRD_v1.docx) — authoritative product spec
- [PROJECT_PLAN.md](docs/PROJECT_PLAN.md) — phased implementation plan
- [FILE_STRUCTURE.md](docs/FILE_STRUCTURE.md) — canonical repo layout
- [CLAUDE.md](CLAUDE.md) — repo-wide conventions
- [docs/adr/](docs/adr/) — architecture decision records
- [docs/runbooks/](docs/runbooks/) — on-call and operational guides
