import pytest

import migrate_to_pasarguard as migration


def test_env_flag_defaults_to_false(monkeypatch):
    monkeypatch.delenv(migration.PASARGUARD_MIGRATION_INSECURE_TLS_ENV, raising=False)

    assert migration._env_flag(migration.PASARGUARD_MIGRATION_INSECURE_TLS_ENV) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_flag_accepts_common_truthy_values(monkeypatch, value):
    monkeypatch.setenv(migration.PASARGUARD_MIGRATION_INSECURE_TLS_ENV, value)

    assert migration._env_flag(migration.PASARGUARD_MIGRATION_INSECURE_TLS_ENV) is True


class _DummyResponse:
    status_code = 200

    def json(self) -> dict:
        return {"access_token": "token"}


class _DummyAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return _DummyResponse()


@pytest.mark.asyncio
async def test_panel_client_verifies_tls_by_default(monkeypatch):
    calls: list[dict] = []

    def _factory(*args, **kwargs):
        calls.append(kwargs)
        return _DummyAsyncClient(*args, **kwargs)

    monkeypatch.setattr(migration.httpx, "AsyncClient", _factory)

    client = migration.PanelClient("https://panel.example.com", "admin", "secret")

    await client.authenticate()

    assert calls[0]["verify"] is True


@pytest.mark.asyncio
async def test_panel_client_can_disable_tls_verification_for_legacy_mode(monkeypatch):
    calls: list[dict] = []

    def _factory(*args, **kwargs):
        calls.append(kwargs)
        return _DummyAsyncClient(*args, **kwargs)

    monkeypatch.setattr(migration.httpx, "AsyncClient", _factory)

    client = migration.PanelClient(
        "https://panel.example.com",
        "admin",
        "secret",
        verify_tls=False,
    )

    await client.authenticate()

    assert calls[0]["verify"] is False
