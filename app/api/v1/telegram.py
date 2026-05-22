from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.dependencies import get_current_admin
from app.services.telegram_notify import TelegramNotifyService

router = APIRouter()


class DirectMessageBody(BaseModel):
    chat_id: int
    text: str
    parse_mode: str = "HTML"


@router.get("/bot-info", summary="Get Telegram bot info")
async def bot_info(_: str = Depends(get_current_admin)) -> dict:
    notify = TelegramNotifyService()
    info = await notify.get_bot_info()
    if not info:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to reach Telegram API",
        )
    return info


@router.post("/send", summary="Send direct message via bot")
async def send_direct(
    body: DirectMessageBody,
    _: str = Depends(get_current_admin),
) -> dict:
    notify = TelegramNotifyService()
    ok = await notify.send_message(body.chat_id, body.text, body.parse_mode)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to send message"
        )
    return {"detail": "sent"}
