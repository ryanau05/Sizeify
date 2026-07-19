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

## Run the capstone demo

**This branch (`demo/capstone`) carries a working end-to-end prototype** — paste a partner-brand
shirt URL, get a size recommendation built from a seeded closet. It runs against the real
matching engine; only the scraper and LLM edges are stubbed. See [DEMO_PLAN.md](docs/DEMO_PLAN.md)
for scope and [DEMO_TICKETS.md](docs/DEMO_TICKETS.md) for status.

**Prerequisites:** Docker (this project is developed against [colima](https://github.com/abiosoft/colima) —
`colima start`, *not* Docker Desktop), `uv`, and Node 18+.

```bash
# 1. Config. Defaults in both examples work as-is for local dev.
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env

# 2. Infra + migrations + seed + npm install, in one command.
./scripts/demo.sh
```

`scripts/demo.sh` prints a pre-authenticated demo JWT. **Paste it into `apps/web/.env` as
`VITE_DEMO_JWT=`** — this is what lets the demo skip a login screen. Then, in two terminals:

```bash
make demo-api    # FastAPI on :8000 (DEMO_MODE=1; separate ASGI app from production)
make demo-web    # Vite on :5173  ← open this
```

**Two URLs to try** (both are seeded fixtures; full list in
[`apps/api/src/api/demo/fixtures/README.md`](apps/api/src/api/demo/fixtures/README.md)):

| Paste into *Find my size* | Shows |
|---|---|
| `https://www.jcrew.com/p/bowery-dress-shirt` | Confident pick — **M, 73%**, with fit notes and the shirts it reasoned from |
| `https://bananarepublic.com/p/grant-slim-non-iron-shirt` | Low confidence — **M vs L, 56%**, so it shows *both* candidates with the trade-off (PRD §5.4) |

Any non-partner hostname returns a 422 "not a partner brand yet" — that path is intentional.
The *My closet* tab lists the seeded closet and can add a shirt: enter measurements plus
free-text feedback like `perfect chest but collar is tight`, and the stubbed extractor turns it
into structured fit signals.

**Two gotchas.** The seeded JWT expires 15 minutes after seeding — re-run `make demo-seed`,
re-paste the token, and **restart `make demo-web`** (Vite reads `.env` only at startup). And
adding garments changes the fit profile, so confidence numbers drift from the table above;
`make db-reset && make demo-seed` returns to the documented state.

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
