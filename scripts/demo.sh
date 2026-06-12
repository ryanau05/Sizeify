#!/usr/bin/env bash
# DEMO bootstrap (capstone, throwaway). Brings the demo stack to "ready to
# present" in one command. Isolated from production: only touches local infra,
# the demo seed, and prints next steps. Safe to delete with the demo.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> [1/4] Local infra (Postgres + Redis)…"
docker compose -f infra/docker-compose.yml up -d

echo "==> [2/4] Migrations…"
( cd apps/api && uv run alembic upgrade head )

echo "==> [3/4] Seeding demo user + closet + brand products…"
( cd apps/api && DEMO_MODE=1 uv run python -m api.demo.seed_demo )

echo "==> [4/4] Web client deps…"
( cd apps/web && npm install --silent )

cat <<'EOF'

✅ Demo stack ready. Open two terminals:

  make demo-api    # backend  -> http://localhost:8000  (DEMO_MODE=1)
  make demo-web    # frontend -> http://localhost:5173

Demo login is pre-seeded: copy the JWT printed above into apps/web/.env
(VITE_DEMO_JWT=...) to skip the login screen.

Headline flow: open the web app -> "Find my size" -> paste a partner-brand
product URL (e.g. a jcrew.com or uniqlo.com shirt) -> recommendation.
EOF
