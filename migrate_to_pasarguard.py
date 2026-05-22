#!/usr/bin/env python3
"""
Standalone migration script: Bot DB (PostgreSQL) → PasarGuard Panel.

Reads active VPN keys from the bot's database and re-creates
corresponding users on a fresh PasarGuard/Marzban panel via REST API.
Optionally updates access_url in the bot DB with new subscription links.

Usage:
    # Dry-run (no changes, just show what would happen):
    python migrate_to_pasarguard.py --dry-run

    # Actual migration:
    python migrate_to_pasarguard.py

    # With explicit credentials:
    python migrate_to_pasarguard.py \
        --db-url "postgresql://user:pass@localhost:5432/vpnbot" \
        --panel-url "https://panel.example.com:8012" \
        --panel-login admin \
        --panel-password secret

    # Update access_url in DB after migration:
    python migrate_to_pasarguard.py --update-db

    # Also restore expired keys (disabled on panel):
    python migrate_to_pasarguard.py --include-expired --update-db

    # Temporary legacy mode for self-signed local panel TLS:
    PASARGUARD_MIGRATION_INSECURE_TLS=1 python migrate_to_pasarguard.py --dry-run

Environment variables (fallback when CLI args not provided):
    DB_ENGINE, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME
    PASARGUARD_ADMIN_PANEL, PASARGUARD_ADMIN_LOGIN, PASARGUARD_ADMIN_PASSWORD
    PASARGUARD_MIGRATION_INSECURE_TLS=1  # disables TLS certificate validation
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Install it: pip install httpx")
    sys.exit(1)

try:
    import asyncpg
except ImportError:
    print("ERROR: asyncpg is required. Install it: pip install asyncpg")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Try loading .env file from the project directory
# ---------------------------------------------------------------------------
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASARGUARD_MIGRATION_INSECURE_TLS_ENV = "PASARGUARD_MIGRATION_INSECURE_TLS"


def _build_db_url(args: argparse.Namespace) -> str:
    """Build asyncpg DSN from CLI args or environment."""
    if args.db_url:
        url = args.db_url
        # asyncpg needs postgresql://, not postgresql+asyncpg://
        return url.replace("postgresql+asyncpg://", "postgresql://")

    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "vpnbot")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def _panel_url(args: argparse.Namespace) -> str:
    url = args.panel_url or os.getenv("PASARGUARD_ADMIN_PANEL", "")
    if not url:
        print(
            "ERROR: Panel URL not provided. Use --panel-url or set PASARGUARD_ADMIN_PANEL"
        )
        sys.exit(1)
    return url.rstrip("/")


def _log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def _env_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _extract_sub_token(access_url: str) -> Optional[str]:
    """Extract the subscription token/path from a full access_url.

    Examples:
        https://panel.com/sub/abc123     -> /sub/abc123
        https://panel.com/sub/abc123/    -> /sub/abc123/
        /sub/abc123                      -> /sub/abc123
    """
    if not access_url:
        return None
    idx = access_url.find("/sub/")
    if idx == -1:
        return None
    return access_url[idx:]


# ---------------------------------------------------------------------------
# PasarGuard API client (minimal, standalone)
# ---------------------------------------------------------------------------


class PanelClient:
    def __init__(
        self,
        base_url: str,
        login: str,
        password: str,
        *,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = base_url
        self.login = login
        self.password = password
        self.verify_tls = verify_tls
        self._token: Optional[str] = None

    def _client(self, timeout: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, verify=self.verify_tls)

    async def authenticate(self) -> None:
        async with self._client(timeout=15) as client:
            resp = await client.post(
                f"{self.base_url}/api/admin/token",
                data={"username": self.login, "password": self.password},
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Auth failed ({resp.status_code}): {resp.text[:300]}"
                )
            self._token = resp.json()["access_token"]
            _log("Authenticated with PasarGuard panel")

    @property
    def _headers(self) -> dict:
        if not self._token:
            raise RuntimeError("Not authenticated — call authenticate() first")
        return {"Authorization": f"Bearer {self._token}"}

    async def get_user(self, username: str) -> Optional[dict]:
        async with self._client(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/api/user/{username}",
                headers=self._headers,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def create_user(
        self,
        username: str,
        expire_iso: Optional[str],
        status: str = "active",
        data_limit: int = 0,
        group_ids: Optional[list] = None,
        old_sub_token: Optional[str] = None,
    ) -> dict:
        uid = str(uuid.uuid4())
        proxy_settings = {
            "vmess": {"id": uid},
            "vless": {"id": uid, "flow": ""},
            "trojan": {"password": uid[:16]},
            "shadowsocks": {
                "password": uid.replace("-", "")[:22],
                "method": "chacha20-ietf-poly1305",
            },
        }
        payload: dict = {
            "username": username,
            "proxy_settings": proxy_settings,
            "expire": expire_iso,
            "data_limit": data_limit,
            "data_limit_reset_strategy": "no_reset",
            "status": status,
        }
        if group_ids:
            payload["group_ids"] = group_ids
        # Try to preserve old subscription token (supported by some
        # PasarGuard/Marzban versions — will be silently ignored if not).
        if old_sub_token:
            payload["subscription_url"] = old_sub_token

        async with self._client(timeout=15) as client:
            resp = await client.post(
                f"{self.base_url}/api/user",
                headers=self._headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_groups(self) -> list[dict]:
        async with self._client(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/api/groups",
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("groups", data if isinstance(data, list) else [])

    async def validate(self) -> bool:
        try:
            async with self._client(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/api/system",
                    headers=self._headers,
                )
                return resp.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Main migration logic
# ---------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> None:
    db_url = _build_db_url(args)
    panel_base = _panel_url(args)
    panel_login = args.panel_login or os.getenv("PASARGUARD_ADMIN_LOGIN", "admin")
    panel_password = args.panel_password or os.getenv("PASARGUARD_ADMIN_PASSWORD", "")

    if not panel_password:
        print(
            "ERROR: Panel password not provided. Use --panel-password or PASARGUARD_ADMIN_PASSWORD"
        )
        sys.exit(1)

    dry_run = args.dry_run
    update_db = args.update_db
    include_expired = args.include_expired
    verify_tls = not _env_flag(PASARGUARD_MIGRATION_INSECURE_TLS_ENV)

    _log(f"Database: {db_url.split('@')[-1]}")  # hide credentials
    _log(f"Panel:    {panel_base}")
    _log(f"Mode:     {'DRY RUN' if dry_run else 'LIVE MIGRATION'}")
    _log(f"Scope:    {'active + expired' if include_expired else 'active only'}")
    _log(f"Update DB access_url: {'yes' if update_db else 'no'}")
    if verify_tls:
        _log("Panel TLS certificate verification: enabled")
    else:
        _log(
            "Panel TLS certificate verification: disabled via "
            f"{PASARGUARD_MIGRATION_INSECURE_TLS_ENV}=1 "
            "(legacy mode for temporary self-signed local environments only)",
            "WARN",
        )
    print()

    # ── 1. Connect to panel ──────────────────────────────────────────────
    panel = PanelClient(
        panel_base,
        panel_login,
        panel_password,
        verify_tls=verify_tls,
    )
    if not dry_run:
        await panel.authenticate()
        if not await panel.validate():
            _log("Panel connection validation failed!", "ERROR")
            sys.exit(1)
        _log("Panel connection OK")

        # Fetch available groups
        groups = await panel.get_groups()
        if groups:
            _log(
                f"Available groups on panel: {json.dumps([g.get('name', g.get('id')) for g in groups])}"
            )

    # ── 2. Connect to database ───────────────────────────────────────────
    _log("Connecting to database...")
    conn = await asyncpg.connect(db_url)
    _log("Database connected")

    # ── 3. Fetch keys to migrate ─────────────────────────────────────────
    status_filter = "('active', 'expired')" if include_expired else "('active')"
    query = f"""
        SELECT
            k.id,
            k.user_id,
            k.pasarguard_key_id,
            k.access_url,
            k.name,
            k.status,
            k.expires_at,
            k.plan_id,
            p.name       AS plan_name,
            p.duration_days
        FROM vpn_keys k
        LEFT JOIN plans p ON p.id = k.plan_id
        WHERE k.status IN {status_filter}
          AND k.pasarguard_key_id IS NOT NULL
        ORDER BY k.id
    """
    rows = await conn.fetch(query)
    _log(f"Found {len(rows)} VPN key(s) to migrate")

    if not rows:
        _log("Nothing to migrate. Exiting.")
        await conn.close()
        return

    # ── 4. Migrate each key ──────────────────────────────────────────────
    created = 0
    skipped = 0
    failed = 0
    already_exists = 0
    urls_preserved = 0
    urls_changed = 0

    for row in rows:
        key_id = row["id"]
        user_id = row["user_id"]
        username = row["pasarguard_key_id"]  # e.g. "vpn_123456_1"
        old_access_url = row["access_url"] or ""
        expires_at = row["expires_at"]
        status = row["status"]
        plan_name = row["plan_name"] or "unknown"

        # Extract old sub token from the stored access_url
        old_sub_token = _extract_sub_token(old_access_url)

        # Determine expiration
        expire_iso = None
        if expires_at:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            expire_iso = expires_at.isoformat()

        # Determine panel status
        panel_status = "active" if status == "active" else "disabled"

        _log(
            f"  [{key_id}] user={user_id} username={username} "
            f"plan={plan_name} status={status} "
            f"expires={expire_iso or 'never'}"
        )
        if old_sub_token:
            _log(f"    old sub token: {old_sub_token}")

        if dry_run:
            _log(
                f"    -> [DRY RUN] Would create user '{username}' (status={panel_status})"
            )
            created += 1
            continue

        # Check if user already exists on panel
        try:
            existing = await panel.get_user(username)
            if existing:
                _log(f"    -> SKIP: user '{username}' already exists on panel", "WARN")
                already_exists += 1

                # Still update access_url if requested
                if update_db:
                    sub_token = existing.get("subscription_url", "")
                    if sub_token:
                        new_url = (
                            sub_token
                            if sub_token.startswith("http")
                            else f"{panel_base}{sub_token.rstrip('/')}"
                        )
                        await conn.execute(
                            "UPDATE vpn_keys SET access_url = $1 WHERE id = $2",
                            new_url,
                            key_id,
                        )
                        _log("    -> Updated access_url in DB")
                continue
        except Exception as e:
            _log(f"    -> Error checking existing user: {e}", "WARN")

        # Create user on panel, passing old sub_token to try to preserve URL
        try:
            result = await panel.create_user(
                username=username,
                expire_iso=expire_iso,
                status=panel_status,
                data_limit=0,
                old_sub_token=old_sub_token,
            )
            created += 1

            # Build the new access_url from the API response
            new_sub_token = result.get("subscription_url", "")
            if new_sub_token:
                new_url = (
                    new_sub_token
                    if new_sub_token.startswith("http")
                    else f"{panel_base}{new_sub_token.rstrip('/')}"
                )
            else:
                new_url = f"{panel_base}/sub/{username}"

            # Compare old vs new URL path
            old_path = _extract_sub_token(old_access_url)
            new_path = _extract_sub_token(new_url)
            if old_path and new_path and old_path.rstrip("/") == new_path.rstrip("/"):
                urls_preserved += 1
                _log("    -> CREATED (sub URL preserved!)")
            else:
                urls_changed += 1
                _log(f"    -> CREATED (sub URL changed: {old_path} -> {new_path})")

            # Update access_url in bot DB
            if update_db:
                await conn.execute(
                    "UPDATE vpn_keys SET access_url = $1 WHERE id = $2",
                    new_url,
                    key_id,
                )
                _log(f"    -> Updated access_url: {new_url[:80]}...")

        except httpx.HTTPStatusError as e:
            failed += 1
            body = e.response.text[:300] if e.response else ""
            _log(f"    -> FAILED ({e.response.status_code}): {body}", "ERROR")
        except Exception as e:
            failed += 1
            _log(f"    -> FAILED: {e}", "ERROR")

        # Small delay to avoid overwhelming the panel
        await asyncio.sleep(0.3)

    await conn.close()

    # ── 5. Summary ───────────────────────────────────────────────────────
    print()
    _log("=" * 50)
    _log("Migration summary:")
    _log(f"  Total keys processed: {len(rows)}")
    _log(f"  Created on panel:     {created}")
    _log(f"  Already existed:      {already_exists}")
    _log(f"  Skipped:              {skipped}")
    _log(f"  Failed:               {failed}")
    if not dry_run and created > 0:
        _log(f"  URLs preserved:       {urls_preserved}")
        _log(f"  URLs changed:         {urls_changed}")
        if urls_changed > 0:
            _log(
                "  NOTE: Some subscription URLs changed. Use --update-db to "
                "save new URLs in the bot DB. Users with changed URLs will "
                "need to re-import their subscription link from the bot.",
                "WARN",
            )
    if dry_run:
        _log("  (DRY RUN -- no actual changes were made)")
    _log("=" * 50)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate VPN users from bot DB to PasarGuard panel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db-url",
        help="PostgreSQL connection URL (overrides env vars)",
    )
    parser.add_argument(
        "--panel-url",
        help="PasarGuard panel URL (overrides PASARGUARD_ADMIN_PANEL)",
    )
    parser.add_argument(
        "--panel-login",
        help="Panel admin login (overrides PASARGUARD_ADMIN_LOGIN)",
    )
    parser.add_argument(
        "--panel-password",
        help="Panel admin password (overrides PASARGUARD_ADMIN_PASSWORD)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Update access_url in bot DB with new subscription links",
    )
    parser.add_argument(
        "--include-expired",
        action="store_true",
        help="Also migrate expired keys (created as disabled on panel)",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
