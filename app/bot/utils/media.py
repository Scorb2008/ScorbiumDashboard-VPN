"""
Утилиты для отправки и редактирования сообщений.
Корректно обрабатывает сообщения с фото (caption) и без (text).
"""

from typing import Optional
import base64

from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    BufferedInputFile,
)
from aiogram.exceptions import TelegramBadRequest


def resolve_photo_input(photo: Optional[str]) -> Optional[str | BufferedInputFile]:
    if not photo:
        return None

    value = str(photo).strip()
    if not value:
        return None

    payload = value
    if value.startswith("data:image/") and "," in value:
        payload = value.split(",", 1)[1].strip()

    try:
        decoded = base64.b64decode(payload, validate=True)
    except Exception:
        return value

    image_signatures = (
        b"\xff\xd8\xff",
        b"\x89PNG\r\n\x1a\n",
        b"GIF87a",
        b"GIF89a",
        b"RIFF",
    )
    if not decoded.startswith(image_signatures):
        return value

    extension = "jpg"
    if decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif decoded.startswith((b"GIF87a", b"GIF89a")):
        extension = "gif"
    elif decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP":
        extension = "webp"

    return BufferedInputFile(decoded, filename=f"bot_photo.{extension}")


async def answer_with_photo(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo: Optional[str] = None,
    parse_mode: str = "HTML",
) -> Message:
    """Отправляет новое сообщение — с фото если есть file_id, иначе текст."""
    photo_input = resolve_photo_input(photo)
    if photo_input:
        try:
            return await message.answer_photo(
                photo=photo_input,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except TelegramBadRequest:
            pass
    return await message.answer(
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def edit_with_photo(
    callback: CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo: Optional[str] = None,
    parse_mode: str = "HTML",
) -> None:
    """
    Редактирует текущее сообщение или отправляет новое.
    - Если передано фото: удаляет старое, шлёт новое с фото.
    - Если фото нет: пробует edit_text, при ошибке (сообщение с фото) — edit_caption,
      при ошибке — удаляет и шлёт новое.
    """
    msg = callback.message

    photo_input = resolve_photo_input(photo)
    if photo_input:
        # Нужно фото — удаляем старое и шлём новое
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await msg.answer_photo(
                photo=photo_input,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except Exception:
            await msg.answer(
                text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
        return

    # Без фото — пробуем редактировать
    # Сначала edit_text (для текстовых сообщений)
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except TelegramBadRequest as e:
        if "there is no text in the message" in str(e):
            # Сообщение с фото — редактируем caption
            try:
                await msg.edit_caption(
                    caption=text, reply_markup=reply_markup, parse_mode=parse_mode
                )
                return
            except Exception:
                pass
        elif "message is not modified" in str(e):
            return  # Ничего не изменилось — ок
    except Exception:
        pass

    # Fallback: удаляем и шлём новое
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        await msg.answer(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        pass


async def safe_edit(
    callback: CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML",
) -> None:
    """Безопасное редактирование без фото — обрабатывает caption и text."""
    await edit_with_photo(
        callback, text, reply_markup=reply_markup, parse_mode=parse_mode
    )
