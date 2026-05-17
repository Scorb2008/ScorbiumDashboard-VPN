#!/usr/bin/env bash
# =============================================================================
#  Fix Alembic Migration Conflicts — Auto-detect & Repair
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET} $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }
err()   { echo -e "${RED}[ERR]${RESET} $*"; exit 1; }

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║     Fix Alembic Migration Conflicts (Auto)                ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check containers
docker ps | grep -q vpn_app || err "Container vpn_app not running. Run: docker compose up -d"
docker ps | grep -q vpn_db  || err "Container vpn_db not running"

info "Current alembic revision:"
docker exec vpn_app uv run alembic current 2>/dev/null || warn "Could not get current revision"

info "Running auto-fix script..."
docker exec vpn_app uv run python fix_alembic.py || err "fix_alembic.py failed"

info "Upgrading to head..."
docker exec vpn_app uv run alembic upgrade head || err "alembic upgrade head failed"

ok "Migrations applied successfully"

echo ""
echo "Verify:"
docker exec vpn_app uv run alembic current
echo ""
echo "Done."
