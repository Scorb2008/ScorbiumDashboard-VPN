"""
Уникальные фичи VPN бота:
- /status    — статус всех подписок + трафик + дней осталось
- /payments  — история платежей пользователя
- /ping      — пинг до VPN серверов (через Marzban nodes)
- /top       — топ рефереров (мотивация приглашать)
- /gift      — подарить подписку другу по username
- Автопродление подписки с баланса
"""

import asyncio
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.database import AsyncSessionFactory
from app.services.vpn_key import VpnKeyService
from app.services.user import UserService
from app.services.referral import ReferralService
from app.services.bot_settings import BotSettingsService
from app.services.payment import PaymentService
from app.services.i18n import t, get_lang

router = Router()


def _fmt_traffic(bytes_val: int) -> str:
    gb = bytes_val / (1024**3)
    if gb >= 1000:
        return f"{gb / 1024:.2f} TB"
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = bytes_val / (1024**2)
    if mb >= 1:
        return f"{mb:.0f} MB"
    return f"{bytes_val} B"


async def _get_traffic_for_key(pasarguard_key_id: str) -> dict | None:
    try:
        from app.services.pasarguard.pasarguard import PasarguardService

        panel = PasarguardService()
        user_data = await panel.get_user(pasarguard_key_id)
        if user_data:
            download = user_data.get("download", 0) or 0
            upload = user_data.get("upload", 0) or 0
            total = download + upload
            return {"used": _fmt_traffic(total), "total_bytes": total}
    except Exception:
        pass
    return None


# ── /status — быстрый статус подписок с трафиком ──────────────────────────────


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_active_for_user(message.from_user.id)
        settings = await BotSettingsService(session).get_all()
        user = await UserService(session).get_by_id(message.from_user.id)
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)

    if not keys:
        await message.answer(
            t("status_title", lang)
            + "\n\n"
            + t("status_no_subs", lang)
            + "\n\n"
            + t("btn_buy", lang)
            + ": /buy",
            parse_mode="HTML",
        )
        return

    now = datetime.now(timezone.utc)
    lines = [f"{t('status_title', lang)}\n"]

    for k in keys:
        name = k.name or f"Подписка #{k.id}"
        lines.append(f"🔑 <b>{name}</b>")

        if k.expires_at:
            delta = k.expires_at - now
            days = delta.days
            hours = delta.seconds // 3600
            if days > 7:
                time_str = t("status_days_left", lang, days=days)
                icon = "🟢"
            elif days > 0:
                time_str = f"{days} дн. {hours} ч."
                icon = "🟡"
            else:
                time_str = t("status_hours_left", lang, hours=hours)
                icon = "🔴"
            exp_str = k.expires_at.strftime("%d.%m.%Y")
            lines.append(
                f"   {icon} {t('status_expires', lang, date=exp_str, time_left=time_str)}"
            )
        else:
            lines.append(f"   🟢 {t('status_lifetime', lang)}")

        if k.pasarguard_key_id:
            traffic = await _get_traffic_for_key(k.pasarguard_key_id)
            if traffic:
                lines.append(
                    f"   {t('status_traffic_used', lang)}: <b>{traffic['used']}</b>"
                )
        lines.append("")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("btn_my_keys", lang), callback_data="my_keys")
    )
    builder.row(InlineKeyboardButton(text=t("btn_buy", lang), callback_data="buy"))

    await message.answer(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "status_cmd")
async def cb_status(callback: CallbackQuery) -> None:
    await callback.answer()
    await cmd_status(callback.message)


# ── /payments — история платежей ─────────────────────────────────────────────


PAYMENT_PROVIDER_LABELS = {
    "yookassa": "pay_provider_yookassa",
    "yookassa_sbp": "pay_provider_yookassa_sbp",
    "cryptobot": "pay_provider_cryptobot",
    "freekassa": "pay_provider_freekassa",
    "platega": "pay_provider_platega",
    "telegram_stars": "pay_provider_telegram_stars",
    "balance": "pay_provider_balance",
    "topup": "pay_provider_balance",
}

PAYMENT_STATUS_LABELS = {
    "succeeded": "pay_status_succeeded",
    "pending": "pay_status_pending",
    "failed": "pay_status_failed",
    "cancelled": "pay_status_failed",
    "refunded": "pay_status_refunded",
}


@router.message(Command("payments", "платежи"))
async def cmd_payments(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        settings = await BotSettingsService(session).get_all()
        user = await UserService(session).get_by_id(message.from_user.id)
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)
        payments = await PaymentService(session).get_all(
            user_id=message.from_user.id, limit=10
        )

    if not payments:
        await message.answer(
            t("payments_title", lang) + "\n\n" + t("payments_empty", lang),
            parse_mode="HTML",
        )
        return

    lines = [f"{t('payments_title', lang)}\n"]

    for p in payments:
        amount = float(p.amount) if p.amount else 0
        date_str = p.created_at.strftime("%d.%m.%Y %H:%M") if p.created_at else "—"

        provider_key = PAYMENT_PROVIDER_LABELS.get(
            p.provider.lower() if p.provider else "", p.provider or "—"
        )
        provider_label = t(provider_key, lang)

        status_key = PAYMENT_STATUS_LABELS.get(
            p.status.lower() if p.status else "", p.status or "—"
        )
        status_label = t(status_key, lang)

        pay_type = "📦" if p.payment_type == "subscription" else "💰"

        lines.append(
            f"{pay_type} <b>{amount:.2f} ₽</b> — {provider_label}\n"
            f"   📅 {date_str} | {status_label}"
        )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("btn_balance", lang), callback_data="balance")
    )
    builder.row(
        InlineKeyboardButton(text=t("back_main", lang), callback_data="back_main")
    )

    await message.answer(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "payments_cmd")
async def cb_payments(callback: CallbackQuery) -> None:
    await callback.answer()
    await cmd_payments(callback.message)


# ── /ping — пинг до серверов ──────────────────────────────────────────────────


@router.message(Command("ping", "серверы"))
async def cmd_ping(message: Message) -> None:
    msg = await message.answer("🔄 Проверяю серверы...")

    from app.services.pasarguard.pasarguard import get_vpn_panel

    try:
        panel = get_vpn_panel()
        from app.services.pasarguard.pasarguard import PasarguardService

        if isinstance(panel, PasarguardService):
            nodes_data = await PasarguardService().get_nodes()
            nodes = (
                nodes_data.get("nodes", [])
                if isinstance(nodes_data, dict)
                else (nodes_data or [])
            )
        else:
            nodes = []
    except Exception:
        nodes = []

    if not nodes:
        try:
            import httpx
            from app.core.config import config

            _pg = config.pasarguard
            base = str(_pg.pasarguard_admin_panel).rstrip("/") if _pg else ""
            start = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=5, verify=True) as client:
                await client.get(f"{base}/api/system")
            ms = int((asyncio.get_event_loop().time() - start) * 1000)
            status_icon = "🟢" if ms < 100 else "🟡" if ms < 300 else "🔴"
            text = f"🌐 <b>Статус серверов</b>\n\n{status_icon} Основной сервер: <b>{ms} мс</b>"
        except Exception:
            text = "🔴 <b>Сервер недоступен</b>"
    else:
        lines = ["🌐 <b>Статус серверов:</b>\n"]
        for node in nodes[:8]:
            status = node.get("status", "unknown")
            name = node.get("name", "Сервер")
            addr = node.get("address", "")
            icon = {
                "connected": "🟢",
                "connecting": "🟡",
                "error": "🔴",
                "disabled": "⚫",
            }.get(status, "❓")
            lines.append(
                f"{icon} <b>{name}</b>" + (f" — <code>{addr}</code>" if addr else "")
            )
        text = "\n".join(lines)

    try:
        await msg.edit_text(text, parse_mode="HTML")
    except Exception:
        await message.answer(text, parse_mode="HTML")


# ── /top — топ рефереров ──────────────────────────────────────────────────────


async def _build_top_text(user_id: int) -> str:
    async with AsyncSessionFactory() as session:
        top = await ReferralService(session).get_top(limit=10)
        my_count = await ReferralService(session).count_referrals(user_id)
        user = await UserService(session).get_by_id(user_id)
        ref_code = user.referral_code if user else None

    if not top:
        return "👥 Рейтинг пока пуст. Приглашай друзей первым!"

    medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["🏆 <b>Топ рефереров</b>\n"]

    for i, r in enumerate(top):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        uname = (
            f"@{r['username']}"
            if r.get("username")
            else r.get("full_name") or f"<code>{r['user_id']}</code>"
        )
        is_me = " ← вы" if r["user_id"] == user_id else ""
        lines.append(f"{medal} {uname} — <b>{r['referral_count']}</b> реф.{is_me}")

    lines.append(f"\n👤 Ваш результат: <b>{my_count}</b> рефералов")

    if ref_code:
        lines.append(f"\n🔗 Ваш реф. код: <code>{ref_code}</code>")

    return "\n".join(lines)


@router.message(Command("top", "рейтинг"))
async def cmd_top(message: Message) -> None:
    text = await _build_top_text(message.from_user.id)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("gift"))
async def cmd_gift(message: Message) -> None:
    """
    /gift @username — подарить активную подписку другому пользователю.
    Списывает стоимость с баланса дарителя.
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "🎁 <b>Подарить подписку</b>\n\n"
            "Использование: <code>/gift @username</code>\n\n"
            "Стоимость подарка списывается с вашего баланса.",
            parse_mode="HTML",
        )
        return

    target_username = args[1].lstrip("@").strip()

    async with AsyncSessionFactory() as session:
        from sqlalchemy import select
        from app.models.user import User

        result = await session.execute(
            select(User).where(User.username == target_username)
        )
        target = result.scalar_one_or_none()

        if not target:
            await message.answer(
                f"❌ Пользователь @{target_username} не найден в системе."
            )
            return

        if target.id == message.from_user.id:
            await message.answer("❌ Нельзя подарить подписку самому себе.")
            return

        sender = await UserService(session).get_by_id(message.from_user.id)
        balance = float(sender.balance or 0) if sender else 0.0

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"🎁 Подарить @{target_username}",
            callback_data=f"gift:confirm:{target.id}:{target_username}",
        )
    )
    builder.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="back_main"))

    await message.answer(
        f"🎁 <b>Подарить подписку</b>\n\n"
        f"Получатель: @{target_username}\n"
        f"Ваш баланс: <b>{balance:.2f} ₽</b>\n\n"
        f"Выберите тариф для подарка:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("gift:confirm:"))
async def gift_select_plan(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    target_id = int(parts[2])
    target_username = parts[3]

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService

        plans = await PlanService(session).get_all(only_active=True)
        sender = await UserService(session).get_by_id(callback.from_user.id)
        balance = float(sender.balance or 0) if sender else 0.0

    builder = InlineKeyboardBuilder()
    for plan in plans:
        if balance >= float(plan.price):
            builder.row(
                InlineKeyboardButton(
                    text=f"🎁 {plan.name} — {plan.price} ₽",
                    callback_data=f"gift:buy:{target_id}:{plan.id}",
                )
            )
    builder.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="back_main"))

    try:
        await callback.message.edit_text(
            f"🎁 Подарок для @{target_username}\n💰 Ваш баланс: {balance:.2f} ₽\n\nВыберите тариф:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("gift:buy:"))
async def gift_buy(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    target_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.vpn_key import VpnKeyService
        from app.services.telegram_notify import TelegramNotifyService

        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        sender = await UserService(session).deduct_balance(
            callback.from_user.id, plan.price
        )
        if not sender:
            await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
            return

        key = await VpnKeyService(session).provision(user_id=target_id, plan=plan)

        if not key:
            await session.rollback()
            await callback.answer("❌ Ошибка создания ключа", show_alert=True)
            return

        await session.commit()

        target = await UserService(session).get_by_id(target_id)
        target_name = (
            f"@{target.username}"
            if target and target.username
            else f"<code>{target_id}</code>"
        )

    if key:
        await TelegramNotifyService().send_message(
            target_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\n"
            f"От: @{callback.from_user.username or callback.from_user.first_name}\n"
            f"Тариф: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка подписки:</b>\n<code>{key.access_url}</code>",
        )
        try:
            await callback.message.edit_text(
                f"✅ <b>Подарок отправлен!</b>\n\n"
                f"Получатель: {target_name}\n"
                f"Тариф: <b>{plan.name}</b>\n"
                f"Списано: <b>{plan.price} ₽</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await callback.answer("❌ Ошибка создания ключа", show_alert=True)

    await callback.answer()


# ── Автопродление ─────────────────────────────────────────────────────────────


@router.message(Command("autorenew", "автопродление"))
async def cmd_autorenew(message: Message) -> None:
    """Информация об автопродлении."""
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(message.from_user.id)
        balance = float(user.balance or 0) if user else 0.0
        keys = await VpnKeyService(session).get_active_for_user(message.from_user.id)

    if not keys:
        await message.answer("📦 Нет активных подписок для автопродления.")
        return

    lines = [
        "🔄 <b>Автопродление подписок</b>\n",
        f"💰 Ваш баланс: <b>{balance:.2f} ₽</b>\n",
        "При истечении подписки система автоматически спишет стоимость с баланса и продлит её.\n",
        "<b>Ваши подписки:</b>",
    ]
    for k in keys:
        exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
        price = f"{k.price} ₽" if k.price else "—"
        lines.append(f"• {k.name or f'Подписка #{k.id}'} — до {exp}, цена: {price}")

    lines.append("\n💡 Пополни баланс чтобы автопродление работало.")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))

    await message.answer(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "top_referrers")
async def cb_top(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await _build_top_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, parse_mode="HTML")


# ── Серверы ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "servers")
async def cb_servers(callback: CallbackQuery) -> None:
    await callback.answer()

    async with AsyncSessionFactory() as session:
        from app.services.i18n import get_lang

        settings = await BotSettingsService(session).get_all()
        user = await UserService(session).get_by_id(callback.from_user.id)
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)

        try:
            from app.services.pasarguard.pasarguard import PasarguardService

            svc = PasarguardService()
            result = await svc.get_nodes()

            if isinstance(result, list):
                nodes = result
            elif isinstance(result, dict):
                nodes = result.get("nodes", result.get("items", []))
            else:
                nodes = []
        except Exception:
            nodes = []

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back", callback_data="back_main"
            )
        )

        if not nodes:
            text = (
                "🌐 <b>Серверы</b>\n\nИнформация о серверах недоступна."
                if lang == "ru"
                else "🌐 <b>Servers</b>\n\nServer info unavailable."
            )
        else:
            lines = [
                "🌐 <b>Серверы VPN</b>\n" if lang == "ru" else "🌐 <b>VPN Servers</b>\n"
            ]

            for node in nodes:
                name = node.get("name", "—")
                status = node.get("status", "")
                address = node.get("address", "")
                icon = "🟢" if status in ("connected", "healthy", "online") else "🔴"
                lines.append(
                    f"{icon} <b>{name}</b>"
                    + (f"\n   <code>{address}</code>" if address else "")
                )

            text = "\n".join(lines)

        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback=callback, text=text, reply_markup=builder.as_markup()
        )


# ── /extend — продление через баланс ─────────────────────────────────────────


@router.message(Command("extend", "продлить"))
async def cmd_extend(message: Message) -> None:
    """Показывает тарифы для продления активной подписки."""
    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_active_for_user(message.from_user.id)
        user = await UserService(session).get_by_id(message.from_user.id)
        from app.services.plan import PlanService

        plans = await PlanService(session).get_all(only_active=True)
        balance = float(user.balance or 0) if user else 0.0

    if not keys:
        await message.answer(
            "📦 Нет активных подписок для продления.\n\nИспользуй /buy чтобы купить новую.",
            parse_mode="HTML",
        )
        return

    if not plans:
        await message.answer("😔 Нет доступных тарифов.")
        return

    builder = InlineKeyboardBuilder()
    for plan in plans:
        can_afford = "✅" if balance >= float(plan.price) else "💰"
        builder.row(
            InlineKeyboardButton(
                text=f"{can_afford} {plan.name} — {plan.price} ₽ ({plan.duration_days} дн.)",
                callback_data=f"plan:{plan.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))

    await message.answer(
        f"🔄 <b>Продление подписки</b>\n\n"
        f"💰 Ваш баланс: <b>{balance:.2f} ₽</b>\n\n"
        f"Выберите тариф для продления:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
