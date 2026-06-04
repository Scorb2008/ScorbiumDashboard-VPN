from unittest.mock import AsyncMock

import pytest
from fastapi import Request

from app.api.panel.routes import nodes as nodes_routes
from app.api.panel.routes import subscriptions as subscriptions_routes
from app.api.panel.routes import users as users_routes
from app.services.user import UserService


def _make_request(path: str = "/panel/users") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_user_service_search_matches_telegram_id(session, sample_user):
    results = await UserService(session).search(str(sample_user.id))

    assert any(user.id == sample_user.id for user in results)


@pytest.mark.asyncio
async def test_users_search_returns_id_matches(session, sample_user, monkeypatch):
    monkeypatch.setattr(
        users_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )

    response = await users_routes.users_search(
        request=_make_request(),
        q=str(sample_user.id),
        db=session,
    )

    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert f'user-row-{sample_user.id}' in body
    assert str(sample_user.id) in body


@pytest.mark.asyncio
async def test_add_balance_route_commits_and_sends_notification(
    session, sample_user, monkeypatch
):
    sent: list[tuple[int, str]] = []
    commit_mock = AsyncMock(wraps=session.commit)
    initial_balance = float(sample_user.balance or 0)

    class FakeNotify:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    monkeypatch.setattr(
        users_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )
    monkeypatch.setattr(session, "commit", commit_mock)
    monkeypatch.setattr(users_routes, "TelegramNotifyService", lambda: FakeNotify())

    response = await users_routes.add_balance(
        user_id=sample_user.id,
        request=_make_request(f"/panel/users/{sample_user.id}/add-balance"),
        amount=100,
        db=session,
    )

    await session.refresh(sample_user)

    assert response.status_code == 200
    assert float(sample_user.balance) == initial_balance + 100.0
    commit_mock.assert_awaited_once()
    assert sent == [(sample_user.id, "💰 На ваш баланс зачислено <b>100 ₽</b>")]


def test_node_status_meta_maps_extended_pasarguard_states():
    assert nodes_routes._node_status_meta("disconnected")[2] == "Офлайн"
    assert nodes_routes._node_status_meta("healthy")[2] == "Подключена"
    assert nodes_routes._node_status_meta("syncing")[3] == "connecting"


@pytest.mark.asyncio
async def test_delete_subscription_hwid_rerenders_modal(
    session, sample_vpn_key, monkeypatch
):
    deleted: list[tuple[str, str]] = []

    class FakePanel:
        async def get_hwids_by_username(self, username):
            assert username == sample_vpn_key.pasarguard_key_id
            return {
                "hwids": [
                    {
                        "hwid": "device-a",
                        "device_model": "Pixel 9",
                        "device_os": "Android",
                        "os_version": "15",
                    }
                ],
                "count": 1,
            }

        async def delete_hwid_from_username(self, username, hwid):
            deleted.append((username, hwid))
            return {"hwids": [], "count": 0}

    monkeypatch.setattr(
        subscriptions_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )
    monkeypatch.setattr(subscriptions_routes, "get_vpn_panel", lambda: FakePanel())

    response = await subscriptions_routes.delete_subscription_hwid(
        key_id=sample_vpn_key.id,
        request=_make_request(f"/panel/subscriptions/{sample_vpn_key.id}/hwids/delete"),
        hwid="device-a",
        db=session,
    )

    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert deleted == [(sample_vpn_key.pasarguard_key_id, "device-a")]
    assert "Для этой подписки пока не зарегистрированы устройства." in body
