import json
from dataclasses import dataclass
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.bot.handlers.admin import _is_admin
from app.core.database import AsyncSessionFactory
from app.models.payment import PaymentStatus
from app.services.vpn_key import VpnKeyService
from app.services.bot_settings import BotSettingsService
from app.services.i18n import t

router = Router()

CONNECT_GUIDES = {
    "ios": (
        "📱 <b>Подключение на iOS</b>\n\n"
        "1. Установи <b>Streisand</b> или <b>V2Box</b> из App Store\n"
        "2. Открой приложение → «+» → «Импорт из буфера обмена»\n"
        "3. Вставь свою ссылку подписки\n"
        "4. Нажми «Подключить» ✅\n\n"
        "💡 Рекомендуем: <b>Streisand</b> (бесплатно, без рекламы)"
    ),
    "android": (
        "🤖 <b>Подключение на Android</b>\n\n"
        "1. Установи <b>V2RayNG</b> из Google Play или APK\n"
        "2. Нажми «+» → «Импорт конфигурации из буфера обмена»\n"
        "3. Вставь ссылку подписки\n"
        "4. Нажми ▶️ для подключения ✅\n\n"
        "💡 Альтернатива: <b>Hiddify</b>"
    ),
    "windows": (
        "🖥 <b>Подключение на Windows</b>\n\n"
        "1. Скачай <b>Hiddify</b> или <b>v2rayN</b> с GitHub\n"
        "2. Открой → «Добавить подписку» → вставь ссылку\n"
        "3. Нажми «Обновить» → выбери сервер → «Подключить» ✅\n\n"
        "💡 Рекомендуем: <b>Hiddify Next</b>"
    ),
    "macos": (
        "🍎 <b>Подключение на macOS</b>\n\n"
        "1. Установи <b>FoXray</b> или <b>Hiddify</b> из Mac App Store\n"
        "2. Добавь подписку → вставь ссылку\n"
        "3. Выбери сервер → «Подключить» ✅\n\n"
        "💡 Альтернатива: <b>V2RayXS</b>"
    ),
    "linux": (
        "🐧 <b>Подключение на Linux</b>\n\n"
        "1. Установи <b>Hiddify</b>:\n"
        "<code>flatpak install flathub app.hiddify.com.HiddifyDesktop</code>\n\n"
        "2. Или используй <b>v2ray-core</b> + конфиг вручную\n"
        "3. Добавь ссылку подписки в приложение ✅\n\n"
        "💡 Для CLI: <b>sing-box</b>"
    ),
}


@dataclass
class KeyRow:
    id: int
    name: str
    status_val: str
    expires_str: str
    access_url: str
    price: str


async def _get_lang(user_id: int, session) -> str:
    from app.services.user import UserService
    from app.services.bot_settings import BotSettingsService
    from app.services.i18n import get_lang

    user = await UserService(session).get_by_id(user_id)
    settings = await BotSettingsService(session).get_all()
    user_lang = user.language if user and user.language else None
    return get_lang(settings, user_lang)


def _extension_already_applied_text(lang: str) -> str:
    return {
        "ru": "Продление уже применено",
        "en": "Extension already applied",
        "fa": "تمدید قبلا اعمال شده است",
    }.get(lang, "Extension already applied")


async def _complete_extension_payment(
    user_id: int, payment_id: int, plan_id: int, key_id: int, external_id: str
) -> str | None:
    from app.services.payment import PaymentService
    from app.services.payment_fulfillment import PaymentFulfillmentService
    from app.services.plan import PlanService

    async with AsyncSessionFactory() as session:
        payment = await PaymentService(session).get_by_id(payment_id)
        plan = await PlanService(session).get_by_id(plan_id)
        if not payment or not plan or payment.user_id != user_id:
            return None

        await PaymentService(session).confirm_once(payment_id, external_id)
        result = await PaymentFulfillmentService(session).extend_subscription_once(
            payment_id, user_id, key_id, plan
        )
        await session.commit()

        if not result.key:
            return None
        return (
            result.key.expires_at.strftime("%d.%m.%Y") if result.key.expires_at else "—"
        )


# ── Мои подписки ──────────────────────────────────────────────────────────────


@router.callback_query(F.data == "my_keys")
async def show_my_keys(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang(callback.from_user.id, session)
        all_keys = await VpnKeyService(session).get_all_for_user(callback.from_user.id)
        kb_menu = await _get_menu_kb(
            session,
            lang=lang,
            user_id=callback.from_user.id,
            is_admin=_is_admin(callback.from_user.id),
        )
        photo = await BotSettingsService(session).get("photo_my_keys")

        active_rows, archive_rows = [], []
        for k in all_keys:
            status_val = k.status.value if hasattr(k.status, "value") else str(k.status)
            exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
            row = KeyRow(
                id=k.id,
                name=k.name or f"Подписка #{k.id}",
                status_val=status_val,
                expires_str=exp,
                access_url=k.access_url or "",
                price=str(k.price or ""),
            )
            if status_val == "active":
                active_rows.append(row)
            else:
                archive_rows.append(row)

    from app.bot.utils.media import edit_with_photo

    if not active_rows and not archive_rows:
        try:
            await edit_with_photo(
                callback,
                t("no_keys", lang),
                reply_markup=kb_menu,
                photo=photo or None,
            )
        except Exception:
            pass
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    if active_rows:
        for row in active_rows:
            builder.row(
                InlineKeyboardButton(
                    text=f"✅ {row.name} — до {row.expires_str}",
                    callback_data=f"key:detail:{row.id}",
                )
            )
    else:
        builder.row(
            InlineKeyboardButton(text=t("no_keys_buy", lang), callback_data="buy")
        )

    if archive_rows:
        builder.row(
            InlineKeyboardButton(
                text=t("archive_btn", lang, count=len(archive_rows)),
                callback_data="key:archive",
            )
        )

    builder.row(
        InlineKeyboardButton(text=t("btn_about", lang), callback_data="about"),
        InlineKeyboardButton(text=t("btn_connect", lang), callback_data="connect:menu"),
    )
    builder.row(
        InlineKeyboardButton(text=t("back_main", lang), callback_data="back_main")
    )

    text = t("my_keys_title", lang) + "\n\n"
    if active_rows:
        text += t("active_count", lang, count=len(active_rows)) + "\n"
    if archive_rows:
        text += t("archive_count", lang, count=len(archive_rows)) + "\n"

    try:
        await edit_with_photo(
            callback, text, reply_markup=builder.as_markup(), photo=photo or None
        )
    except Exception:
        pass
    await callback.answer()


# ── Архив ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "key:archive")
async def show_archive(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang(callback.from_user.id, session)
        all_keys = await VpnKeyService(session).get_all_for_user(callback.from_user.id)

        archive_rows = []
        for k in all_keys:
            status_val = k.status.value if hasattr(k.status, "value") else str(k.status)
            if status_val != "active":
                exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
                archive_rows.append(
                    KeyRow(
                        id=k.id,
                        name=k.name or f"Подписка #{k.id}",
                        status_val=status_val,
                        expires_str=exp,
                        access_url="",
                        price="",
                    )
                )

    if not archive_rows:
        await callback.answer(t("archive_empty_alert", lang), show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    icons = {"expired": "⏰", "revoked": "❌"}
    for row in archive_rows:
        icon = icons.get(row.status_val, "❓")
        builder.row(
            InlineKeyboardButton(
                text=f"{icon} {row.name} — {row.expires_str}",
                callback_data=f"key:detail:{row.id}",
            )
        )
    builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="my_keys"))

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            t("archive_title", lang, count=len(archive_rows)),
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await callback.answer()


# ── Детали ────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("key:detail:"))
async def show_key_detail(callback: CallbackQuery) -> None:
    key_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        lang = await _get_lang(callback.from_user.id, session)
        key = await VpnKeyService(session).get_by_id(key_id)
        if not key or key.user_id != callback.from_user.id:
            await callback.answer(t("sub_not_found", lang), show_alert=True)
            return

        status_val = (
            key.status.value if hasattr(key.status, "value") else str(key.status)
        )
        exp = key.expires_at.strftime("%d.%m.%Y %H:%M") if key.expires_at else "—"
        name = key.name or f"Подписка #{key.id}"
        access_url = key.access_url or ""
        price = str(key.price or "")
        plan_name = key.plan.name if key.plan else name

    status_label = {
        "active": t("status_active", lang),
        "expired": t("status_expired", lang),
        "revoked": t("status_revoked", lang),
    }.get(status_val, "❓")

    text = (
        f"📦 <b>{plan_name}</b>\n\n"
        f"{t('key_detail_status', lang)} {status_label}\n"
        f"{t('key_detail_expires', lang)} <b>{exp}</b>\n"
    )
    if price:
        text += f"{t('key_detail_price', lang)} <b>{price} ₽</b>\n"

    if access_url:
        text += f"\n{t('key_detail_link', lang)}\n<code>{access_url}</code>\n\n{t('key_detail_hint', lang)}"
    else:
        text += f"\n{t('key_detail_no_url', lang)}"

    builder = InlineKeyboardBuilder()
    if access_url:
        builder.row(
            InlineKeyboardButton(
                text=t("btn_how_connect", lang), callback_data="connect:menu"
            )
        )
    if status_val == "active":
        builder.row(
            InlineKeyboardButton(
                text="🔄 Продлить подписку", callback_data=f"key:extend:{key_id}"
            )
        )
    back_cb = "my_keys" if status_val == "active" else "key:archive"
    builder.row(InlineKeyboardButton(text=t("back", lang), callback_data=back_cb))

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(callback, text, reply_markup=builder.as_markup())
    except Exception:
        pass
    await callback.answer()


# ── О проекте ─────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "about")
async def about_project(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang(callback.from_user.id, session)
        settings = await BotSettingsService(session).get_all()

    about_text = settings.get("about_text") or (
        "🌐 <b>О нашем VPN-сервисе</b>\n\n"
        "⚡️ Высокая скорость без ограничений\n"
        "🔒 Полная анонимность и шифрование\n"
        "🌍 Серверы в разных странах\n"
        "📱 Работает на всех устройствах\n"
        "🛡 Протоколы: VLESS, VMess, Shadowsocks\n\n"
        "💬 Поддержка 24/7 — всегда на связи\n"
        "🎁 Реферальная программа — приглашай друзей и получай бонусы"
    )
    photo = settings.get("photo_about") or None

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("btn_connect", lang), callback_data="connect:menu")
    )
    builder.row(InlineKeyboardButton(text=t("btn_buy_sub", lang), callback_data="buy"))
    builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="my_keys"))

    from app.bot.utils.media import edit_with_photo

    try:
        await edit_with_photo(
            callback, about_text, reply_markup=builder.as_markup(), photo=photo
        )
    except Exception:
        pass
    await callback.answer()


# ── Как подключить ────────────────────────────────────────────────────────────


@router.callback_query(F.data == "connect:menu")
async def connect_menu(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang(callback.from_user.id, session)
        photo = await BotSettingsService(session).get("photo_connect")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📱 iOS", callback_data="connect:ios"),
        InlineKeyboardButton(text="🤖 Android", callback_data="connect:android"),
    )
    builder.row(
        InlineKeyboardButton(text="🖥 Windows", callback_data="connect:windows"),
        InlineKeyboardButton(text="🍎 macOS", callback_data="connect:macos"),
    )
    builder.row(InlineKeyboardButton(text="🐧 Linux", callback_data="connect:linux"))
    builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="my_keys"))

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            t("connect_title", lang),
            reply_markup=builder.as_markup(),
            photo=photo or None,
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("connect:"))
async def connect_guide(callback: CallbackQuery) -> None:
    platform = callback.data.split(":")[1]
    if platform == "menu":
        return

    async with AsyncSessionFactory() as session:
        lang = await _get_lang(callback.from_user.id, session)
        photo = await BotSettingsService(session).get("photo_connect")

    guide = CONNECT_GUIDES.get(platform)
    if not guide:
        await callback.answer(t("connect_not_found", lang), show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="◀️ Назад к устройствам", callback_data="connect:menu")
    )
    builder.row(
        InlineKeyboardButton(text=t("btn_my_subs", lang), callback_data="my_keys")
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback, guide, reply_markup=builder.as_markup(), photo=photo or None
        )
    except Exception:
        pass
    await callback.answer()


# ── Продление подписки ───────────────────────────────────────────────────


@router.callback_query(F.data.startswith("key:extend:"))
async def extend_key(callback: CallbackQuery) -> None:
    key_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.user import UserService

        lang = await _get_lang(callback.from_user.id, session)
        key = await VpnKeyService(session).get_by_id(key_id)
        if not key or key.user_id != callback.from_user.id:
            await callback.answer(t("sub_not_found", lang), show_alert=True)
            return

        status_val = (
            key.status.value if hasattr(key.status, "value") else str(key.status)
        )
        if status_val not in ("active", "expired"):
            await callback.answer("Подписка недоступна для продления", show_alert=True)
            return

        plans = await PlanService(session).get_all(only_active=True)
        user = await UserService(session).get_by_id(callback.from_user.id)
        balance = float(user.balance or 0) if user else 0

    if not plans:
        await callback.answer("Нет доступных тарифов", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for plan in plans:
        price = float(plan.price or 0)
        can_pay = balance >= price
        if can_pay:
            builder.row(
                InlineKeyboardButton(
                    text=f"💰 {plan.name} — {price}₽ ({plan.duration_days} дн.) с баланса",
                    callback_data=f"extend:pay:{key_id}:{plan.id}",
                )
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=f"{plan.name} — {price}₽ ({plan.duration_days} дн.)",
                    callback_data=f"extend:methods:{key_id}:{plan.id}",
                )
            )

    builder.row(
        InlineKeyboardButton(text=t("back", lang), callback_data=f"key:detail:{key_id}")
    )

    text = "🔄 <b>Продлить подписку</b>\n\n"
    text += f"Текущая: {key.name or f'Подписка #{key.id}'}\n"
    text += f"Баланс: <b>{balance:.2f} ₽</b>\n\n"
    if balance > 0:
        text += "Выберите тариф для оплаты с баланса или для других способов:"
    else:
        text += "Выберите тариф для оплаты:"

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(callback, text, reply_markup=builder.as_markup())
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:methods:"))
async def extend_choose_method(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.user import UserService
        from app.services.bot_settings import BotSettingsService
        from app.services.telegram_stars import TelegramStarsService

        plan = await PlanService(session).get_by_id(plan_id)
        user = await UserService(session).get_by_id(callback.from_user.id)
        settings = await BotSettingsService(session).get_all()
        balance = float(user.balance or 0) if user else 0

        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        _yk_toggle = (
            await BotSettingsService(session).get("ps_yookassa_enabled") or "0"
        ) == "1"
        _sbp_toggle = (
            await BotSettingsService(session).get("ps_sbp_enabled") or "0"
        ) == "1"
        _yk_shop_db = (
            await BotSettingsService(session).get("yookassa_shop_id_override") or ""
        )
        _yk_key_db = bool(
            await BotSettingsService(session).get("yookassa_secret_key_override")
        )
        _yk_configured = bool(_yk_shop_db and _yk_key_db)
        has_yookassa = _yk_toggle and _yk_configured
        has_sbp = _sbp_toggle and _yk_configured

        _cb_toggle = (
            await BotSettingsService(session).get("ps_cryptobot_enabled") or "0"
        ) == "1"
        has_cryptobot = bool(settings.get("cryptobot_token", "").strip()) and _cb_toggle

        _fk_toggle = (
            await BotSettingsService(session).get("ps_freekassa_enabled") or "0"
        ) == "1"
        _fk_shop = await BotSettingsService(session).get("freekassa_shop_id") or ""
        _fk_key = await BotSettingsService(session).get("freekassa_api_key") or ""
        has_freekassa = _fk_toggle and bool(_fk_shop and _fk_key)

        _pl_toggle = (
            await BotSettingsService(session).get("ps_platega_enabled") or "0"
        ) == "1"
        _pl_merchant = (
            await BotSettingsService(session).get("platega_merchant_id") or ""
        )
        _pl_secret = await BotSettingsService(session).get("platega_secret") or ""
        has_platega = _pl_toggle and bool(_pl_merchant and _pl_secret)

        _stars_rate = float(
            await BotSettingsService(session).get("stars_rate") or "1.5"
        )
        stars = TelegramStarsService.rub_to_stars(float(plan.price), rate=_stars_rate)

    plan_price = float(plan.price)

    builder = InlineKeyboardBuilder()
    if has_yookassa:
        builder.row(
            InlineKeyboardButton(
                text="💳 Банковская карта",
                callback_data=f"extend:yookassa:{key_id}:{plan_id}",
            )
        )
    if has_sbp:
        builder.row(
            InlineKeyboardButton(
                text="🏦 СБП",
                callback_data=f"extend:sbp:{key_id}:{plan_id}",
            )
        )
    if has_freekassa:
        builder.row(
            InlineKeyboardButton(
                text="💸 FreeKassa",
                callback_data=f"extend:freekassa:{key_id}:{plan_id}",
            )
        )
    if has_platega:
        builder.row(
            InlineKeyboardButton(
                text="🟦 Platega",
                callback_data=f"extend:platega:{key_id}:{plan_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=f"⭐ Telegram Stars ({stars} ⭐)",
            callback_data=f"extend:stars:{key_id}:{plan_id}",
        )
    )
    if has_cryptobot:
        builder.row(
            InlineKeyboardButton(
                text="₿ Криптовалюта",
                callback_data=f"extend:crypto:{key_id}:{plan_id}",
            )
        )
    if balance > 0 and balance >= plan_price:
        builder.row(
            InlineKeyboardButton(
                text=f"💰 С баланса ({balance:.2f} ₽)",
                callback_data=f"extend:pay:{key_id}:{plan_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=f"key:extend:{key_id}",
        )
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            f"💳 <b>Оплата продления</b>\n\n{plan.name} — {plan.price} ₽ ({plan.duration_days} дн.)\n\nВыберите способ оплаты:",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:yookassa:"))
async def extend_yookassa(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.yookassa import YookassaService
        from app.services.payment import PaymentService
        from app.models.payment import PaymentProvider

        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        yk = await YookassaService.create()
        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.YOOKASSA,
        )
        payment.meta = json.dumps({"extend_key_id": str(key_id)})
        await session.flush()
        payment_id = payment.id

        me = await bot.get_me()
        return_url = f"https://t.me/{me.username}"
        yk_payment = await yk.create_payment(
            amount=plan.price,
            description=f"Продление подписки {plan.name}",
            return_url=return_url,
            metadata={
                "payment_id": str(payment.id),
                "plan_id": str(plan.id),
                "extend_key_id": str(key_id),
            },
        )
        payment.external_id = yk_payment.id
        await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Оплатить", url=yk_payment.confirmation.confirmation_url
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Проверить оплату",
            callback_data=f"extend:check:yk:{payment_id}:{plan_id}:{key_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад", callback_data=f"extend:methods:{key_id}:{plan_id}"
        )
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            f"💳 <b>Продление подписки</b>\n\n{plan.name} — {plan.price} ₽\n\nПосле оплаты нажмите «Проверить».",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:sbp:"))
async def extend_sbp(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.yookassa import YookassaService
        from app.services.payment import PaymentService
        from app.models.payment import PaymentProvider

        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        yk = await YookassaService.create()
        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.YOOKASSA_SBP,
        )
        payment.meta = json.dumps({"extend_key_id": str(key_id)})
        await session.flush()
        payment_id = payment.id

        me = await bot.get_me()
        return_url = f"https://t.me/{me.username}"
        yk_payment = await yk.create_sbp_payment(
            amount=plan.price,
            description=f"Продление подписки {plan.name}",
            return_url=return_url,
            metadata={
                "payment_id": str(payment.id),
                "plan_id": str(plan.id),
                "extend_key_id": str(key_id),
            },
        )
        payment.external_id = yk_payment.id
        await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Оплатить", url=yk_payment.confirmation.confirmation_url
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Проверить оплату",
            callback_data=f"extend:check:yk:{payment_id}:{plan_id}:{key_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад", callback_data=f"extend:methods:{key_id}:{plan_id}"
        )
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            f"🏦 <b>Продление через СБП</b>\n\n{plan.name} — {plan.price} ₽\n\nПосле оплаты нажмите «Проверить».",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:stars:"))
async def extend_stars(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.payment import PaymentService
        from app.services.telegram_stars import TelegramStarsService
        from app.models.payment import PaymentProvider

        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        stars = TelegramStarsService.rub_to_stars(
            float(plan.price),
            rate=float(await BotSettingsService(session).get("stars_rate") or "1.5"),
        )
        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.TELEGRAM_STARS,
        )
        payment.meta = json.dumps({"extend_key_id": str(key_id)})
        await session.commit()

    ok = await TelegramStarsService(bot).send_invoice(
        chat_id=callback.from_user.id,
        title=f"Продление подписки {plan.name}",
        description=f"{plan.duration_days} дней",
        payload=f"extend_stars:{payment.id}:{plan_id}:{key_id}",
        stars_amount=stars,
    )

    try:
        if ok:
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"⭐ Оплата продления: {stars} ⭐",
                reply_markup=InlineKeyboardBuilder()
                .row(
                    InlineKeyboardButton(
                        text="Назад", callback_data=f"extend:methods:{key_id}:{plan_id}"
                    )
                )
                .as_markup(),
            )
        else:
            await callback.answer("Ошибка создания инвойса", show_alert=True)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:crypto:"))
async def extend_crypto(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.payment import PaymentService
        from app.services.cryptobot import CryptoBotService
        from app.services.bot_settings import BotSettingsService
        from app.models.payment import PaymentProvider

        plan = await PlanService(session).get_by_id(plan_id)
        settings = await BotSettingsService(session).get_all()
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        crypto = CryptoBotService.from_settings(settings)
        if not crypto:
            await callback.answer("CryptoBot не настроен", show_alert=True)
            return

        usdt_amount = await crypto.rub_to_usdt(float(plan.price))
        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.CRYPTOBOT,
        )
        payment.meta = json.dumps({"extend_key_id": str(key_id)})
        await session.flush()

        invoice = await crypto.create_invoice(
            amount=usdt_amount,
            currency="USDT",
            description=f"Продление подписки {plan.name}",
            payload=f"extend_crypto:{payment.id}:{plan_id}:{key_id}",
        )
        if not invoice:
            await session.rollback()
            await callback.answer("Ошибка создания инвойса", show_alert=True)
            return

        payment.external_id = str(invoice["invoice_id"])
        await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Оплатить", url=invoice["pay_url"]))
    builder.row(
        InlineKeyboardButton(
            text="Проверить",
            callback_data=f"extend:check:crypto:{payment.id}:{plan_id}:{key_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад", callback_data=f"extend:methods:{key_id}:{plan_id}"
        )
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            f"₿ <b>Продление криптой</b>\n\n{plan.name} — {plan.price} ₽ (~{usdt_amount} USDT)",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:freekassa:"))
async def extend_freekassa(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.payment import PaymentService
        from app.services.bot_settings import BotSettingsService
        from app.services.freekassa import FreeKassaService
        from app.models.payment import PaymentProvider

        plan = await PlanService(session).get_by_id(plan_id)
        settings = await BotSettingsService(session).get_all()
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        fk = FreeKassaService.from_settings(settings)
        if not fk:
            await callback.answer("FreeKassa не настроен", show_alert=True)
            return

        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.FREEKASSA,
        )
        payment.meta = json.dumps({"extend_key_id": str(key_id)})
        await session.flush()
        payment_id = payment.id

        order_id = f"fk_ext_{payment_id}_{plan_id}_{key_id}"
        pay_url = fk.create_payment_url(
            order_id=order_id,
            amount=float(plan.price),
            currency="RUB",
            lang="ru",
        )

        payment.external_id = order_id
        await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Оплатить", url=pay_url))
    builder.row(
        InlineKeyboardButton(
            text="Проверить",
            callback_data=f"extend:check:fk:{payment_id}:{plan_id}:{key_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад", callback_data=f"extend:methods:{key_id}:{plan_id}"
        )
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            f"🟢 <b>Продление через FreeKassa</b>\n\n{plan.name} — {plan.price} ₽\n\nПосле оплаты нажмите «Проверить».",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:platega:"))
async def extend_platega(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.payment import PaymentService
        from app.services.bot_settings import BotSettingsService
        from app.services.platega import PlategaService
        from app.models.payment import PaymentProvider

        plan = await PlanService(session).get_by_id(plan_id)
        settings = await BotSettingsService(session).get_all()
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        platega = PlategaService.from_settings(settings)
        if not platega:
            await callback.answer("Platega не настроен", show_alert=True)
            return

        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.PLATEGA,
        )
        payment.meta = json.dumps({"extend_key_id": str(key_id)})
        await session.flush()
        payment_id = payment.id

        me = await bot.get_me()
        return_url = f"https://t.me/{me.username}"
        transaction = await platega.create_transaction(
            amount=float(plan.price),
            currency="RUB",
            description=f"Продление подписки {plan.name}",
            return_url=return_url,
            failed_url=return_url,
            payload_data=f"pl_{payment_id}_{plan_id}_{key_id}",
            user_telegram_id=str(callback.from_user.id),
            user_id=str(callback.from_user.id),
        )
        if not transaction.get("ok") or not transaction.get("url"):
            await session.rollback()
            await callback.answer("Ошибка создания платежа", show_alert=True)
            return

        payment.external_id = str(transaction.get("transaction_id") or "")
        await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Оплатить", url=transaction["url"]))
    builder.row(
        InlineKeyboardButton(
            text="Проверить",
            callback_data=f"extend:check:platega:{payment_id}:{plan_id}:{key_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад", callback_data=f"extend:methods:{key_id}:{plan_id}"
        )
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(
            callback,
            f"🟦 <b>Продление через Platega</b>\n\n{plan.name} — {plan.price} ₽\n\nПосле оплаты нажмите «Проверить».",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("extend:check:fk:"))
async def extend_check_fk(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[3])
    plan_id = int(parts[4])
    key_id = int(parts[5])

    async with AsyncSessionFactory() as session:
        from app.services.payment import PaymentService
        from app.services.bot_settings import BotSettingsService
        from app.services.freekassa import FreeKassaService

        lang = await _get_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("Платёж не найден", show_alert=True)
            return

        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await callback.answer(
                _extension_already_applied_text(lang), show_alert=True
            )
            return

        settings = await BotSettingsService(session).get_all()
        fk = FreeKassaService.from_settings(settings)
        if not fk:
            await callback.answer("Ошибка", show_alert=True)
            return

        if payment.external_id:
            result = await fk.get_orders(payment.external_id)
            if result and result.get("orders"):
                order = result["orders"][0]
                if order.get("orderStatus") == 1:
                    exp = await _complete_extension_payment(
                        callback.from_user.id,
                        payment_id,
                        plan_id,
                        key_id,
                        payment.external_id,
                    )
                    if exp:
                        await callback.answer(f"Продлено до {exp}!", show_alert=True)
                    else:
                        await callback.answer("Ошибка продления", show_alert=True)
                else:
                    await callback.answer("Ожидание оплаты...", show_alert=True)
            else:
                await callback.answer("Ожидание оплаты...", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("extend:check:platega:"))
async def extend_check_platega(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[3])
    plan_id = int(parts[4])
    key_id = int(parts[5])

    async with AsyncSessionFactory() as session:
        from app.services.payment import PaymentService
        from app.services.bot_settings import BotSettingsService
        from app.services.platega import PlategaService

        lang = await _get_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("Платёж не найден", show_alert=True)
            return

        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await callback.answer(
                _extension_already_applied_text(lang), show_alert=True
            )
            return

        settings = await BotSettingsService(session).get_all()
        platega = PlategaService.from_settings(settings)
        if not platega or not payment.external_id:
            await callback.answer("Ошибка", show_alert=True)
            return

        transaction = await platega.get_transaction_status(payment.external_id)
        if transaction.get("ok") and PlategaService.is_success_status(
            transaction.get("status", "")
        ):
            exp = await _complete_extension_payment(
                callback.from_user.id,
                payment_id,
                plan_id,
                key_id,
                str(transaction.get("transaction_id") or payment.external_id),
            )
            if exp:
                await callback.answer(f"Продлено до {exp}!", show_alert=True)
            else:
                await callback.answer("Ошибка продления", show_alert=True)
        else:
            await callback.answer("Ожидание оплаты...", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("extend:check:yk:"))
async def extend_check_yk(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[3])
    plan_id = int(parts[4])
    key_id = int(parts[5])

    async with AsyncSessionFactory() as session:
        from app.services.payment import PaymentService

        lang = await _get_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("Платёж не найден", show_alert=True)
            return

        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await callback.answer(
                _extension_already_applied_text(lang), show_alert=True
            )
            return

        if payment.external_id:
            from app.services.yookassa import YookassaService

            yk = await YookassaService.create()
            yk_payment = await yk.get_payment(payment.external_id)
            if yk_payment.status == "succeeded":
                exp = await _complete_extension_payment(
                    callback.from_user.id,
                    payment_id,
                    plan_id,
                    key_id,
                    yk_payment.id,
                )
                if exp:
                    await callback.answer(f"Продлено до {exp}!", show_alert=True)
                else:
                    await callback.answer("Ошибка продления", show_alert=True)
            else:
                await callback.answer("Ожидание оплаты...", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("extend:check:crypto:"))
async def extend_check_crypto(callback: CallbackQuery, bot) -> None:
    parts = callback.data.split(":")
    if len(parts) >= 7:
        payment_id = int(parts[5])
        key_id = int(parts[6])
        plan_id = int(parts[4]) if parts[4].isdigit() else 0
    else:
        payment_id = int(parts[3])
        plan_id = int(parts[4])
        key_id = int(parts[5])

    async with AsyncSessionFactory() as session:
        from app.services.bot_settings import BotSettingsService
        from app.services.cryptobot import CryptoBotService
        from app.services.payment import PaymentService
        from app.services.vpn_key import VpnKeyService

        lang = await _get_lang(callback.from_user.id, session)
        settings = await BotSettingsService(session).get_all()
        crypto = CryptoBotService.from_settings(settings)
        if not crypto:
            await callback.answer("Ошибка", show_alert=True)
            return

        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await callback.answer(
                _extension_already_applied_text(lang), show_alert=True
            )
            return
        if not payment.external_id:
            await callback.answer("Ошибка", show_alert=True)
            return
        if not plan_id:
            key = await VpnKeyService(session).get_by_id(key_id)
            plan_id = int(key.plan_id) if key and key.plan_id else 0
            if not plan_id:
                await callback.answer("Тариф не найден", show_alert=True)
                return

        invoice = await crypto.get_invoice(int(payment.external_id))
        if invoice and invoice.get("status") == "paid":
            exp = await _complete_extension_payment(
                callback.from_user.id,
                payment_id,
                plan_id,
                key_id,
                str(invoice.get("invoice_id") or payment.external_id),
            )
            if exp:
                await callback.answer(f"Продлено до {exp}!", show_alert=True)
            else:
                await callback.answer("Ошибка продления", show_alert=True)
        else:
            await callback.answer("Ожидание оплаты...", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("extend:pay:"))
async def extend_pay(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    key_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.models.payment import PaymentProvider
        from app.services.payment import PaymentService
        from app.services.payment_fulfillment import PaymentFulfillmentService
        from app.services.plan import PlanService
        from app.services.user import UserService
        from decimal import Decimal

        lang = await _get_lang(callback.from_user.id, session)
        key = await VpnKeyService(session).get_by_id(key_id)
        plan = await PlanService(session).get_by_id(plan_id)
        user = await UserService(session).get_by_id(callback.from_user.id)

        if not key or not plan or not user:
            await callback.answer("Ошибка", show_alert=True)
            return

        if key.user_id != callback.from_user.id:
            await callback.answer("Ошибка доступа", show_alert=True)
            return

        balance = float(user.balance or 0)
        price = float(plan.price or 0)

        if balance < price:
            await callback.answer("Недостаточно баланса", show_alert=True)
            return

        # Списываем баланс
        updated = await UserService(session).deduct_balance(
            callback.from_user.id, Decimal(str(price))
        )
        if not updated:
            await callback.answer("Ошибка списания", show_alert=True)
            return

        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.BALANCE,
        )
        await PaymentService(session).confirm_once(
            payment.id, f"balance_extend_{payment.id}"
        )
        result = await PaymentFulfillmentService(session).extend_subscription_once(
            payment.id, callback.from_user.id, key_id, plan
        )
        extended = result.key
        if not extended:
            await UserService(session).add_balance(
                callback.from_user.id, Decimal(str(price))
            )
            await PaymentService(session).fail(payment.id)
        await session.commit()

        if extended:
            exp = (
                extended.expires_at.strftime("%d.%m.%Y") if extended.expires_at else "—"
            )
            text = "✅ <b>Подписка продлена!</b>\n\n"
            text += f"Тариф: {plan.name}\n"
            text += f"Дней: {plan.duration_days}\n"
            text += f"Списано: {price} ₽\n"
            text += f"Новая дата: {exp}"
        else:
            text = "❌ Ошибка продления. Баланс возвращён."

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔑 Мои подписки", callback_data="my_keys"))
    builder.row(
        InlineKeyboardButton(text=t("back_main", lang), callback_data="back_main")
    )

    try:
        from app.bot.utils.media import edit_with_photo

        await edit_with_photo(callback, text, reply_markup=builder.as_markup())
    except Exception:
        pass
    await callback.answer()
