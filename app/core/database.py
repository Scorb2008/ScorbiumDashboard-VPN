from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typing import AsyncGenerator
import asyncio
import socket
import sqlalchemy as sa

from app.core.config import config
from app.utils.log import log


DB_STARTUP_RETRIES = 8
DB_STARTUP_DELAY = 3


def _build_dsn() -> str:
    db = config.database
    password = db.db_password.get_secret_value()
    return (
        f"postgresql+asyncpg://{db.db_user}:{password}"
        f"@{db.db_host}:{db.db_port}/{db.db_name}"
    )


engine = create_async_engine(
    _build_dsn(),
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
    connect_args={"timeout": 10, "command_timeout": 60},
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for DB sessions."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _check_dns(host: str, port: int) -> bool:
    """Resolve hostname to catch DNS issues early with a clear error."""
    try:
        socket.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        return True
    except socket.gaierror:
        return False


async def init_db() -> None:
    """Verify DB connection on startup with retries and clear error messages."""
    import app.models  # noqa: F401 — ensure all models are imported

    db = config.database
    log.info("Connecting to DB at %s:%s ...", db.db_host, db.db_port)

    for attempt in range(1, DB_STARTUP_RETRIES + 1):
        if not _check_dns(db.db_host, db.db_port):
            log.warning("DNS resolution failed for %s:%s (attempt %d/%d)",
                        db.db_host, db.db_port, attempt, DB_STARTUP_RETRIES)
            if attempt == DB_STARTUP_RETRIES:
                log.error(
                    "Cannot resolve DB host '%s'. Check DB_HOST in .env. "
                    "In Docker Compose it should be 'db' (service name), not 'localhost'.",
                    db.db_host)
                raise RuntimeError(f"DNS resolution failed for {db.db_host}:{db.db_port}")
            delay = DB_STARTUP_DELAY * (2 ** (attempt - 1))
            log.info("Retrying in %ds...", delay)
            await asyncio.sleep(delay)
            continue

        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
                # Early schema sanity-check: panel/auth relies on users table.
                # If migrations were applied partially, fail fast with actionable logs.
                exists = await conn.execute(
                    sa.text("""
                        SELECT EXISTS (
                          SELECT 1 FROM information_schema.tables
                          WHERE table_schema='public' AND table_name='users'
                        )
                    """)
                )
                try:
                    users_exists = bool(exists.scalar_one())
                except Exception:
                    users_exists = False
                if not users_exists:
                    raise RuntimeError(
                        'DB schema mismatch: relation "users" does not exist. '
                        "Run alembic migrations (e.g. docker exec vpn_app uv run alembic upgrade head) "
                        "or fix alembic_version with fix_alembic.py."
                    )
            log.info("Database connection verified")
            return
        except Exception as e:
            err_str = str(e).lower()

            if "password" in err_str or "authentication" in err_str:
                log.error(
                    "DB authentication failed for user '%s'.\n"
                    "  Fix: Ensure DB_PASSWORD in .env matches POSTGRES_PASSWORD in docker-compose.\n"
                    "  If you changed .env after first start, run:\n"
                    "    docker compose down -v && docker compose up -d db app",
                    db.db_user)
                raise RuntimeError("DB password authentication failed") from e

            if "connection refused" in err_str or "could not connect" in err_str:
                log.warning("DB not ready yet (attempt %d/%d): %s",
                            attempt, DB_STARTUP_RETRIES, e)
                if attempt == DB_STARTUP_RETRIES:
                    log.error("DB never became ready. Check: docker compose logs db")
                    raise RuntimeError("DB connection refused after retries") from e
                delay = DB_STARTUP_DELAY * (2 ** (attempt - 1))
                log.info("Retrying in %ds...", delay)
                await asyncio.sleep(delay)
                continue

            log.error("Unexpected DB error: %s", e)
            raise

    log.error("DB init failed after %d attempts", DB_STARTUP_RETRIES)
    raise RuntimeError("Database initialization failed")


async def close_db() -> None:
    await engine.dispose()
    log.info("Database connection pool closed")
