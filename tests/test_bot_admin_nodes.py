from app.bot.handlers import admin as admin_handlers


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
