"""Handler для пробного периода VPN."""

from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.core.database import AsyncSessionFactory
from app.services.user import UserService
from app.services.bot_settings import BotSettingsService, parse_int_list_setting
from app.services.vpn_key import VpnKeyService
from app.services.i18n import t, get_lang
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.bot.handlers.admin import _is_admin
from app.bot.utils.subscription_links import subscription_link_kb
from app.utils.html_utils import html_code
from app.utils.log import log

router = Router()


async def _get_lang(user_id: int, session) -> str:
    user = await UserService(session).get_by_id(user_id)
    settings = await BotSettingsService(session).get_all()
    user_lang = user.language if user and user.language else None
    return get_lang(settings, user_lang)


@router.callback_query(F.data == "trial")
async def handle_trial(callback: CallbackQuery) -> None:
    from app.bot.utils.media import edit_with_photo

    async with AsyncSessionFactory() as session:
        lang = await _get_lang(callback.from_user.id, session)
        settings = await BotSettingsService(session).get_all()

        if settings.get("trial_enabled", "0") != "1":
            await callback.answer(
                {
                    "ru": "❌ Пробный период недоступен.",
                    "en": "❌ Trial not available.",
                    "fa": "❌ دوره آزمایشی در دسترس نیست.",
                }.get(lang, "❌"),
                show_alert=True,
            )
            return

        trial_days = int(settings.get("trial_days", "3"))

        all_keys = await VpnKeyService(session).get_all_for_user(callback.from_user.id)

        if all_keys:
            msgs = {
                "ru": "❌ Пробный период доступен только новым пользователям без подписок.",
                "en": "❌ Trial is only available for new users without subscriptions.",
                "fa": "❌ دوره آزمایشی فقط برای کاربران جدید بدون اشتراک در دسترس است.",
            }
            await callback.answer(msgs.get(lang, msgs["ru"]), show_alert=True)
            return

        from datetime import datetime, timezone, timedelta
        from app.models.vpn_key import VpnKey, VpnKeyStatus
        from app.services.pasarguard.pasarguard import PasarguardService
        from app.core.config import config

        trial_days = int(settings.get("trial_days", "3"))
        expires_at = datetime.now(timezone.utc) + timedelta(days=trial_days)

        key = VpnKey(
            user_id=callback.from_user.id,
            plan_id=None,
            price=0,
            expires_at=expires_at,
            name={
                "ru": f"Пробный период ({trial_days} дн.)",
                "en": f"Trial ({trial_days} days)",
                "fa": f"آزمایشی ({trial_days} روز)",
            }.get(lang, f"Trial ({trial_days} days)"),
            status=VpnKeyStatus.ACTIVE.value,
            access_url="pending",
        )
        session.add(key)
        await session.flush()

        # Создаём в Marzban
        username = f"trial_{callback.from_user.id}_{key.id}"
        try:
            group_ids: list[int] = []
            raw_groups = settings.get("vpn_group_ids", "")
            if raw_groups:
                group_ids = parse_int_list_setting(raw_groups)

            marzban = PasarguardService()
            marz_user = await marzban.create_user(
                username=username,
                expire_days=trial_days,
                data_limit_gb=0,
                group_ids=group_ids or None,
            )
            sub_token = marz_user.get("subscription_url", "")
            _pg = config.pasarguard
            panel_base = str(_pg.pasarguard_admin_panel).rstrip("/") if _pg else ""
            if sub_token:
                access_url = (
                    sub_token
                    if sub_token.startswith("http")
                    else f"{panel_base}{sub_token.rstrip('/')}"
                )
            else:
                access_url = f"{panel_base}/sub/{username}"

            key.pasarguard_key_id = username
            key.access_url = access_url
        except Exception as e:
            log.error(f"Trial Marzban error for user {callback.from_user.id}: {e}")
            await session.delete(key)
            await session.flush()
            await callback.answer(t("key_error", lang), show_alert=True)
            return

        await session.commit()

    if not key:
        await callback.answer(t("key_error", lang), show_alert=True)
        return

    msgs = {
        "ru": (
            f"🎁 <b>Пробный период активирован!</b>\n\n"
            f"📅 Действует <b>{trial_days} дней</b>\n\n"
            f"🔑 <b>Ссылка подписки:</b>\n{html_code(key.access_url)}\n\n"
            f"💡 Скопируй ссылку и вставь в VPN-клиент\n\n"
            f"⚠️ Пробный период предоставляется один раз."
        ),
        "en": (
            f"🎁 <b>Trial period activated!</b>\n\n"
            f"📅 Valid for <b>{trial_days} days</b>\n\n"
            f"🔑 <b>Subscription link:</b>\n{html_code(key.access_url)}\n\n"
            f"💡 Copy the link and paste into your VPN client\n\n"
            f"⚠️ Trial is provided once only."
        ),
        "fa": (
            f"🎁 <b>دوره آزمایشی فعال شد!</b>\n\n"
            f"📅 معتبر برای <b>{trial_days} روز</b>\n\n"
            f"🔑 <b>لینک اشتراک:</b>\n{html_code(key.access_url)}\n\n"
            f"💡 لینک را کپی کرده و در کلاینت VPN وارد کنید\n\n"
            f"⚠️ دوره آزمایشی فقط یک بار ارائه می‌شود."
        ),
    }

    async with AsyncSessionFactory() as session:
        kb = await _get_menu_kb(
            session,
            lang=lang,
            user_id=callback.from_user.id,
            is_admin=_is_admin(callback.from_user.id),
        )
        photo_trial = (await BotSettingsService(session).get("photo_trial")) or None

    if key.access_url:
        kb = subscription_link_kb(key.access_url, lang=lang)

    await edit_with_photo(
        callback, msgs.get(lang, msgs["ru"]), reply_markup=kb, photo=photo_trial
    )
    await callback.answer()
