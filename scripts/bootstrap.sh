#!/usr/bin/env bash
#
# Idempotent dev-environment bootstrap for Sizeify.
#
# Safe to run repeatedly. Steps:
#   1. Verify host prerequisites (uv, Docker, Xcode CLI tools, JDK 17).
#   2. uv sync for the backend API.
#   3. Bring up local infra (Postgres + Redis) and wait until healthy.
#   4. Run Alembic migrations.
#   5. Install pre-commit git hooks.
#
# Prereqs are *checked*, not installed — the script prints how to install any
# that are missing and exits non-zero. See README.md.

set -euo pipefail

# --- locate repo root (so the script works from anywhere) -------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="infra/docker-compose.yml"

# --- pretty output ----------------------------------------------------------
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; RED=""; YELLOW=""; RESET=""
fi
step() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
ok()   { printf '%s  ✓ %s%s\n' "$GREEN" "$1" "$RESET"; }
warn() { printf '%s  ! %s%s\n' "$YELLOW" "$1" "$RESET"; }
die()  { printf '%s  ✗ %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
step "Checking prerequisites"

MISSING=0
need() {
  # need <command> <install-hint>
  if command -v "$1" >/dev/null 2>&1; then
    ok "$1 found ($(command -v "$1"))"
  else
    warn "$1 NOT found — install with: $2"
    MISSING=1
  fi
}

need uv "brew install uv  (or https://docs.astral.sh/uv/)"
need docker "brew install colima docker docker-compose && colima start"

# Xcode command-line tools (macOS only).
if [[ "$(uname -s)" == "Darwin" ]]; then
  if xcode-select -p >/dev/null 2>&1; then
    ok "Xcode CLI tools found ($(xcode-select -p))"
  else
    warn "Xcode CLI tools NOT found — install with: xcode-select --install"
    MISSING=1
  fi
fi

# JDK 17 — required by the Android Gradle daemon (gradle-daemon-jvm.properties).
# Must be *exactly* 17: `java_home -v 17` returns the highest JDK >= 17, so we
# validate the major version. The brew keg-only install (referenced by
# apps/android/gradle.properties) is checked first since that's what Gradle uses.
is_jdk17() { [[ -x "$1/bin/java" ]] && "$1/bin/java" -version 2>&1 | grep -q 'version "17'; }

JDK17_HOME=""
for candidate in \
  "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home" \
  "/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home" \
  "$(/usr/libexec/java_home -v 17 2>/dev/null || true)"; do
  if [[ -n "$candidate" ]] && is_jdk17 "$candidate"; then
    JDK17_HOME="$candidate"; break
  fi
done
if [[ -n "$JDK17_HOME" ]]; then
  ok "JDK 17 found ($JDK17_HOME)"
else
  warn "JDK 17 NOT found — install with: brew install openjdk@17"
  MISSING=1
fi

# Verify the Docker daemon is actually reachable (not just the CLI present).
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon is running"
  else
    warn "Docker CLI present but daemon not reachable — start it with: colima start"
    MISSING=1
  fi
fi

[[ "$MISSING" -eq 0 ]] || die "Missing prerequisites (see above). Fix them and re-run."

# ---------------------------------------------------------------------------
# 2. Backend dependencies
# ---------------------------------------------------------------------------
step "Syncing backend dependencies (uv sync)"
( cd apps/api && uv sync )
ok "apps/api dependencies synced"

# ---------------------------------------------------------------------------
# 3. Local infrastructure
# ---------------------------------------------------------------------------
step "Starting local infra (Postgres + Redis)"
docker compose -f "$COMPOSE_FILE" up -d

printf '  waiting for services to report healthy'
deadline=$(( SECONDS + 120 ))
while :; do
  healthy="$(docker compose -f "$COMPOSE_FILE" ps --format '{{.Health}}' 2>/dev/null | grep -c healthy || true)"
  [[ "$healthy" -ge 2 ]] && break
  [[ "$SECONDS" -ge "$deadline" ]] && { printf '\n'; die "infra did not become healthy within 120s"; }
  printf '.'; sleep 2
done
printf '\n'
ok "Postgres + Redis healthy"

# ---------------------------------------------------------------------------
# 4. Database migrations
# ---------------------------------------------------------------------------
step "Applying database migrations (alembic upgrade head)"
( cd apps/api && uv run alembic upgrade head )
ok "Migrations applied"

# ---------------------------------------------------------------------------
# 5. Git hooks
# ---------------------------------------------------------------------------
step "Installing pre-commit hooks"
if command -v pre-commit >/dev/null 2>&1; then
  pre-commit install
  ok "pre-commit hook installed"
else
  warn "pre-commit not found — skipping (install with: brew install pre-commit)"
fi

step "Bootstrap complete ✅"
printf 'Next: %smake lint%s and %smake test%s\n' "$BOLD" "$RESET" "$BOLD" "$RESET"
