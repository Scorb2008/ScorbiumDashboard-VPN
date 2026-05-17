from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_current_admin
from app.schemas.user import UserDetail, UserRead, UserUpdate
from app.schemas.payment import PaymentRead
from app.schemas.vpn import VpnKeyRead
from app.services.user import UserService
from app.services.payment import PaymentService
from app.services.vpn_key import VpnKeyService
from app.services.telegram_notify import TelegramNotifyService
from pydantic import BaseModel

router = APIRouter()


class SendMessageBody(BaseModel):
    text: str
    parse_mode: str = "HTML"


@router.get("/", response_model=list[UserRead], summary="List users")
async def list_users(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[UserRead]:
    return await UserService(db).get_all(limit=limit, offset=offset)


@router.get("/{user_id}/keys", response_model=list[VpnKeyRead], summary="User VPN keys")
async def user_keys(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[VpnKeyRead]:
    return await VpnKeyService(db).get_all_for_user(user_id)


@router.get("/{user_id}/payments", response_model=list[PaymentRead], summary="User payments")
async def user_payments(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[PaymentRead]:
    return await PaymentService(db).get_all(user_id=user_id)


@router.post("/{user_id}/message", summary="Send Telegram message to user")
async def send_message(
    user_id: int,
    body: SendMessageBody,
    _: str = Depends(get_current_admin),
) -> dict:
    notify = TelegramNotifyService()
    ok = await notify.send_message(user_id, body.text, body.parse_mode)
    if not ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to send Telegram message")
    return {"detail": "Message sent"}


@router.get("/{user_id}", response_model=UserDetail, summary="Get user details")
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> UserDetail:
    user = await UserService(db).get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserDetail(
        **UserRead.model_validate(user).model_dump(),
        subscriptions_count=len(user.vpn_keys),
        payments_count=len(user.payments),
        vpn_keys_count=len(user.vpn_keys),
    )


@router.patch("/{user_id}", response_model=UserRead, summary="Update user")
async def update_user(
    user_id: int,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> UserRead:
    user = await UserService(db).update(user_id, data)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("/{user_id}/ban", response_model=UserRead, summary="Ban user")
async def ban_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> UserRead:
    user = await UserService(db).ban(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("/{user_id}/unban", response_model=UserRead, summary="Unban user")
async def unban_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> UserRead:
    user = await UserService(db).unban(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
