import pytest

from app.schemas.user import UserCreate
from app.services.user import UserService


@pytest.mark.asyncio
async def test_sync_telegram_profile_handles_duplicate_create_race(
    session, sample_user, monkeypatch
):
    session.expunge(sample_user)
    service = UserService(session)
    original_get_by_id = service.get_by_id
    calls = {"count": 0}

    async def flaky_get_by_id(user_id: int):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return await original_get_by_id(user_id)

    monkeypatch.setattr(service, "get_by_id", flaky_get_by_id)

    user, created = await service.sync_telegram_profile(
        UserCreate(
            id=sample_user.id,
            username="updated_name",
            full_name="Updated User",
        )
    )

    assert created is False
    assert user.id == sample_user.id
    assert user.username == "updated_name"
    assert user.full_name == "Updated User"
