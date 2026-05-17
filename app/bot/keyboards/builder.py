from typing import Optional
from aiogram.types import InlineKeyboardButton, WebAppInfo


def btn(
    text: str,
    callback_data: str = None,
    url: str = None,
    web_app: str = None,
    style: Optional[str] = None,
    emoji_id: Optional[str] = None,
) -> InlineKeyboardButton:
    kwargs: dict = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if web_app:
        kwargs["web_app"] = WebAppInfo(url=web_app)
    if style in ("danger", "success", "primary"):
        kwargs["style"] = style
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**kwargs)
