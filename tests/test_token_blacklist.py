from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import ProgrammingError

from app.services.token_blacklist import TokenBlacklistService


def _missing_table_error() -> ProgrammingError:
    return ProgrammingError(
        "SELECT * FROM blacklisted_tokens",
        {},
        Exception('relation "blacklisted_tokens" does not exist'),
    )


async def test_is_blacklisted_returns_false_when_table_is_missing():
    session = MagicMock()
    session.execute = AsyncMock(side_effect=_missing_table_error())
    session.rollback = AsyncMock()

    service = TokenBlacklistService(session)

    assert await service.is_blacklisted("jti-1", "1980894188") is False
    session.rollback.assert_awaited_once()


async def test_blacklist_jti_does_not_crash_when_table_is_missing():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock(side_effect=_missing_table_error())
    session.rollback = AsyncMock()

    service = TokenBlacklistService(session)

    await service.blacklist_jti("jti-1", "1980894188")
    session.rollback.assert_awaited_once()
