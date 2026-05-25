"""
Migration script to encrypt existing plaintext secrets in the database.
Run with: uv run python migrate_encrypt_secrets.py
"""
import asyncio

_SENSITIVE_KEYS = {
    "cryptobot_token",
    "freekassa_api_key",
    "freekassa_secret_word_1",
    "freekassa_secret_word_2",
    "aikassa_token",
}


async def main():
    from app.core.database import AsyncSessionFactory
    from app.services.encryption import encrypt_value, is_encrypted

    async with AsyncSessionFactory() as session:
        migrated = 0

        for key in _SENSITIVE_KEYS:
            # Use raw DB query to check actual stored value (not decrypted cache)
            from sqlalchemy import text
            result = await session.execute(
                text("SELECT value FROM bot_settings WHERE key = :key"),
                {"key": key},
            )
            row = result.fetchone()
            if not row:
                continue

            value = row[0]
            if not value:
                continue

            if is_encrypted(value):
                print(f"  {key}: already encrypted ✓")
                continue

            encrypted = encrypt_value(value)
            await session.execute(
                text("UPDATE bot_settings SET value = :val WHERE key = :key"),
                {"val": encrypted, "key": key},
            )
            migrated += 1
            print(f"  {key}: encrypted ✓")

        await session.commit()

    if migrated:
        print(f"\n✅ Migrated {migrated} secret(s)")
    else:
        print("\n✅ All secrets already encrypted")


if __name__ == "__main__":
    asyncio.run(main())
