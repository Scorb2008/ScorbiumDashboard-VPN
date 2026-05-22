"""
Утилиты для отправки и редактирования сообщений.
Корректно обрабатывает сообщения с фото (caption) и без (text).
"""

from typing import Optional
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest


async def answer_with_photo(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo: Optional[str] = None,
    parse_mode: str = "HTML",
) -> Message:
    """Отправляет новое сообщение — с фото если есть file_id, иначе текст."""
    if photo:
        try:
            return await message.answer_photo(
                photo=photo,
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

    if photo:
        # Нужно фото — удаляем старое и шлём новое
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await msg.answer_photo(
                photo=photo,
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
