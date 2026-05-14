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

## Documentation

- [Sizeify_PRD_v1.docx](docs/Sizeify_PRD_v1.docx) — authoritative product spec
- [PROJECT_PLAN.md](docs/PROJECT_PLAN.md) — phased implementation plan
- [FILE_STRUCTURE.md](docs/FILE_STRUCTURE.md) — canonical repo layout
- [CLAUDE.md](CLAUDE.md) — repo-wide conventions
- [docs/adr/](docs/adr/) — architecture decision records
- [docs/runbooks/](docs/runbooks/) — on-call and operational guides
