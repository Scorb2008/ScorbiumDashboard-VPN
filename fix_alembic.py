#!/usr/bin/env python3
"""
Fix Alembic migration conflicts.
Run inside container: docker exec vpn_app uv run python fix_alembic.py
"""
import asyncio
import sys
sys.path.insert(0, '/app')

import asyncpg
from app.core.config import config


async def _get_db_conn():
    try:
        return await asyncpg.connect(
            host=config.database.db_host,
            port=config.database.db_port,
            user=config.database.db_user,
            password=config.database.db_password.get_secret_value(),
            database=config.database.db_name,
        )
    except asyncpg.InvalidPasswordError:
        print()
        print("=" * 60)
        print("ERROR: password authentication failed for user")
        print()
        print("  Пароль БД в .env не совпадает с паролем PostgreSQL.")
        print("  Это бывает если .env создан вручную или изменён")
        print("  после первого запуска контейнера.")
        print()
        print("  Решение:")
        print("    bash setup.sh          # создаст .env заново")
        print("    # или сбросить volume:")
        print("    docker compose down -v && bash setup.sh")
        print("=" * 60)
        sys.exit(1)


async def _table_exists(conn, table_name: str) -> bool:
    row = await conn.fetchrow("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = $1
    """, table_name)
    return row is not None


async def _column_exists(conn, table_name: str, column_name: str) -> bool:
    row = await conn.fetchrow("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = $1
          AND column_name = $2
    """, table_name, column_name)
    return row is not None


async def _index_exists(conn, index_name: str) -> bool:
    row = await conn.fetchrow("""
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = $1
    """, index_name)
    return row is not None


def _determine_target_version(schema: dict[str, bool]) -> str:
    """Pick the latest Alembic revision already represented by the schema."""
    target = '4d5f8377eff0'  # initial
    if schema.get('has_language'):
        target = 'a1b2c3d4e5f6'
    if schema.get('has_autorenew'):
        target = 'b3c4d5e6f7a8'
    if schema.get('has_payment_type'):
        target = 'c4d5e6f7a8b9'
    if schema.get('has_admins'):
        target = 'd5e6f7a8b9c0'
    if schema.get('has_performance_indexes'):
        target = 'c5d6e7f8a9b0'
    if schema.get('has_admin_features'):
        target = 'e6f7a8b9c0d1'
    if schema.get('has_admin_totp'):
        target = 'f1a2b3c4d5e6'
    if schema.get('has_admin_backup_codes'):
        target = 'a1b2c3d4e5f7'
    if schema.get('has_vpn_traffic'):
        target = 'b2c3d4e5f6a7'
    if schema.get('has_blacklisted_tokens'):
        target = 'c6d7e8f9a0b1'
    if schema.get('has_promo_usages'):
        target = 'd7e8f9a0b1c2'
    if schema.get('has_branding_assets'):
        target = 'e8f9a0b1c2d3'
    return target


async def main():
    print("=" * 60)
    print("Alembic Migration Fix")
    print("=" * 60)

    conn = await _get_db_conn()

    # 1. Check alembic_version
    has_av = await _table_exists(conn, 'alembic_version')
    versions = []
    if has_av:
        rows = await conn.fetch("SELECT version_num FROM alembic_version;")
        versions = [r['version_num'] for r in rows]
    print(f"\n1. alembic_version table exists: {has_av}")
    print(f"2. Current versions in DB: {versions}")

    # 2. Check actual schema state
    has_language = await _column_exists(conn, 'users', 'language')
    has_autorenew = await _column_exists(conn, 'users', 'autorenew')
    has_payment_type = await _column_exists(conn, 'payments', 'payment_type')
    has_admins = await _table_exists(conn, 'admins')
    has_vpn_traffic = (
        await _column_exists(conn, 'vpn_keys', 'download')
        and await _column_exists(conn, 'vpn_keys', 'upload')
    )
    has_blacklisted_tokens = await _table_exists(conn, 'blacklisted_tokens')
    has_promo_usages = await _table_exists(conn, 'promo_usages')
    has_branding_assets = await _table_exists(conn, 'branding_assets')
    has_admin_totp = await _column_exists(conn, 'admins', 'totp_secret')
    has_admin_backup_codes = await _column_exists(conn, 'admins', 'backup_codes')
    has_admin_features = (
        await _column_exists(conn, 'users', 'last_seen')
        and await _table_exists(conn, 'audit_log')
    )
    has_performance_indexes = (
        await _index_exists(conn, 'ix_vpn_keys_user_id')
        and await _index_exists(conn, 'ix_payments_user_id')
    )

    print("3. Schema state:")
    print(f"   - users.language   : {has_language}")
    print(f"   - users.autorenew  : {has_autorenew}")
    print(f"   - payments.payment_type: {has_payment_type}")
    print(f"   - admins table     : {has_admins}")
    print(f"   - performance indexes: {has_performance_indexes}")
    print(f"   - admin features   : {has_admin_features}")
    print(f"   - admins.totp_secret: {has_admin_totp}")
    print(f"   - admins.backup_codes: {has_admin_backup_codes}")
    print(f"   - vpn_keys traffic : {has_vpn_traffic}")
    print(f"   - blacklisted_tokens: {has_blacklisted_tokens}")
    print(f"   - promo_usages     : {has_promo_usages}")
    print(f"   - branding_assets  : {has_branding_assets}")

    # 3. Determine target version
    has_any_tables = await _table_exists(conn, 'users') or await _table_exists(conn, 'plans')
    target = None
    if not has_any_tables:
        print("\n4. DB is empty — no tables exist. Will run all migrations from scratch.")
        print("   Skipping alembic_version stamp.")
    else:
        target = _determine_target_version({
            'has_language': has_language,
            'has_autorenew': has_autorenew,
            'has_payment_type': has_payment_type,
            'has_admins': has_admins,
            'has_performance_indexes': has_performance_indexes,
            'has_admin_features': has_admin_features,
            'has_admin_totp': has_admin_totp,
            'has_admin_backup_codes': has_admin_backup_codes,
            'has_vpn_traffic': has_vpn_traffic,
            'has_blacklisted_tokens': has_blacklisted_tokens,
            'has_promo_usages': has_promo_usages,
            'has_branding_assets': has_branding_assets,
        })

        print(f"\n4. Detected target version: {target}")

        # 4. Fix alembic_version
        print("\n" + "=" * 60)
        if not has_av:
            print("Creating alembic_version table...")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL PRIMARY KEY
                );
            """)

        await conn.execute("DELETE FROM alembic_version;")
        await conn.execute("INSERT INTO alembic_version (version_num) VALUES ($1);", target)
        print(f"Stamped alembic_version → {target}")
        print("=" * 60)

    await conn.close()
    print("\n[OK] Now run: docker exec vpn_app uv run alembic upgrade head")


if __name__ == "__main__":
    asyncio.run(main())
