from app.services.health import HealthEntry, HealthService, ServiceStatus


class _ExplodingSettings:
    def __init__(self):
        self.calls = 0

    async def get(self, key: str):
        self.calls += 1
        if key == "notify_monitoring_enabled":
            return "1"
        if key == "notify_cooldown_seconds":
            return "300"
        if key == "notify_on_degraded":
            return "0"
        if key == "notify_chat_ids":
            return ""
        if key == "bot_language":
            return "ru"
        if key.startswith("notify_svc_"):
            return "1"
        raise AssertionError(f"Unexpected settings key: {key}")


class _ExplodingSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_send_alerts_reads_language_before_session_context_exits(monkeypatch):
    service = HealthService()
    failing_entry = HealthEntry("database")
    failing_entry.status = ServiceStatus.DOWN
    failing_entry.message = "db down"
    service._entries = {"database": failing_entry}

    settings = _ExplodingSettings()

    monkeypatch.setattr(
        "app.services.health.AsyncSessionFactory", lambda: _ExplodingSessionContext()
    )
    monkeypatch.setattr(
        "app.services.bot_settings.BotSettingsService", lambda _session: settings
    )

    sent_messages = []

    class _Notify:
        async def send_message(self, chat_id, message):
            sent_messages.append((chat_id, message))

    monkeypatch.setattr("app.services.health.TelegramNotifyService", lambda: _Notify())

    await service.send_alerts()

    assert settings.calls >= 6
    assert sent_messages


class _PaymentSettings:
    def __init__(self, values):
        self.values = values

    async def get(self, key: str):
        return self.values.get(key, "")


class _SessionContext:
    def __init__(self, settings):
        self.settings = settings

    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_payment_checks_mark_disabled_gateways_as_inactive(monkeypatch):
    service = HealthService()
    values = {
        "ps_yookassa_enabled": "0",
        "ps_cryptobot_enabled": "0",
        "ps_freekassa_enabled": "0",
    }

    monkeypatch.setattr(
        "app.services.health.AsyncSessionFactory",
        lambda: _SessionContext(_PaymentSettings(values)),
    )
    monkeypatch.setattr(
        "app.services.bot_settings.BotSettingsService",
        lambda _session: _PaymentSettings(values),
    )

    yookassa = HealthEntry("payment_yookassa")
    cryptobot = HealthEntry("payment_cryptobot")
    freekassa = HealthEntry("payment_freekassa")

    await service._check_yookassa(yookassa)
    await service._check_cryptobot(cryptobot)
    await service._check_freekassa(freekassa)

    assert yookassa.status == ServiceStatus.INACTIVE
    assert cryptobot.status == ServiceStatus.INACTIVE
    assert freekassa.status == ServiceStatus.INACTIVE
