from app.models.bot_settings import BotSettings
from app.services.bot_settings import reset_bot_settings_cache
from app.tasks.payment_tasks import _yookassa_polling_is_configured


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_yookassa_polling_disabled_without_bot_settings(session, monkeypatch):
    await reset_bot_settings_cache()
    monkeypatch.setattr(
        "app.tasks.payment_tasks.AsyncSessionFactory",
        lambda: _SessionFactory(session),
    )

    assert await _yookassa_polling_is_configured() is False


async def test_yookassa_polling_enabled_with_db_credentials(session, monkeypatch):
    await reset_bot_settings_cache()
    session.add_all(
        [
            BotSettings(key="ps_yookassa_enabled", value="1"),
            BotSettings(key="yookassa_shop_id_override", value="123456"),
            BotSettings(key="yookassa_secret_key_override", value="test_secret_12345"),
        ]
    )
    await session.commit()

    monkeypatch.setattr(
        "app.tasks.payment_tasks.AsyncSessionFactory",
        lambda: _SessionFactory(session),
    )

    assert await _yookassa_polling_is_configured() is True
