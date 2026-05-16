# Sizeify

Mobile app that recommends what size to buy in any clothing brand based on garments the user already owns. v1 covers men's button-down shirts across 10 partner brands.

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
