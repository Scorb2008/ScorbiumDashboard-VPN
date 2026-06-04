import pytest

from app.bot.handlers import admin as admin_handlers
from aiogram.exceptions import TelegramBadRequest
from types import SimpleNamespace


def _keyboard_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_admin_keyboard_contains_nodes_section():
    texts = _keyboard_texts(admin_handlers.admin_kb())

    assert "🖥 Ноды" in texts


def test_admin_extended_keyboard_contains_nodes_section():
    texts = _keyboard_texts(admin_handlers.admin_kb_extended())

    assert "🖥 Ноды" in texts


def test_admin_node_status_badge_maps_common_statuses():
    assert admin_handlers._node_status_badge("connected") == ("🟢", "Подключена")
    assert admin_handlers._node_status_badge("syncing") == ("🟡", "Синхронизация")
    assert admin_handlers._node_status_badge("disconnected") == ("🔴", "Офлайн")


@pytest.mark.asyncio
async def test_safe_edit_text_ignores_not_modified():
    class DummyMessage:
        async def edit_text(self, text, **kwargs):
            raise TelegramBadRequest(
                method="editMessageText",
                message="Bad Request: message is not modified: specified new message content and reply markup are exactly the same as a current content and reply markup of the message",
            )

    changed = await admin_handlers._safe_edit_text(DummyMessage(), "same text")

    assert changed is False


@pytest.mark.asyncio
async def test_admin_delete_key_hwid_uses_indexed_hwid_lookup(monkeypatch):
    deleted: list[tuple[str, str]] = []
    refreshed: list[tuple[int, int]] = []

    class FakePanel:
        async def get_hwids_by_username(self, username):
            assert username == "vpn_123_1"
            return {
                "hwids": [
                    {"hwid": "device-a", "device_model": "iPhone"},
                    {"hwid": "device-b", "device_model": "Pixel"},
                ],
                "count": 2,
            }

        async def delete_hwid_from_username(self, username, hwid):
            deleted.append((username, hwid))

    class FakeVpnKeyService:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, key_id):
            return SimpleNamespace(id=key_id, user_id=123, pasarguard_key_id="vpn_123_1")

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyCallback:
        def __init__(self):
            self.from_user = SimpleNamespace(id=1)
            self.data = "adm:delhwid:7:123:1"
            self.answers = []

        async def answer(self, text=None, show_alert=False):
            self.answers.append((text, show_alert))

    async def fake_show_admin_key_hwids(callback, key_id, user_id):
        refreshed.append((key_id, user_id))

    monkeypatch.setattr(admin_handlers, "_is_admin", lambda user_id: True)
    monkeypatch.setattr(admin_handlers, "AsyncSessionFactory", lambda: FakeSessionContext())
    monkeypatch.setattr(admin_handlers, "VpnKeyService", FakeVpnKeyService)
    monkeypatch.setattr(admin_handlers, "get_vpn_panel", lambda: FakePanel())
    monkeypatch.setattr(admin_handlers, "_show_admin_key_hwids", fake_show_admin_key_hwids)

    callback = DummyCallback()
    await admin_handlers.admin_delete_key_hwid(callback)

    assert deleted == [("vpn_123_1", "device-b")]
    assert refreshed == [(7, 123)]
    assert callback.answers[-1] == ("✅ HWID удалён", True)
