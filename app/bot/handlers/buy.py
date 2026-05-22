from aiogram import Router, F
from aiogram.types import CallbackQuery
from app.bot.keyboards.payments import plans_kb, payment_methods_kb
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.bot.handlers.admin import _is_admin
from app.core.database import AsyncSessionFactory
from app.services.plan import PlanService
from app.services.bot_settings import BotSettingsService
from app.services.user import UserService
from app.services.i18n import t, get_lang

router = Router()


async def _get_user_lang(user_id: int, session) -> str:
    user = await UserService(session).get_by_id(user_id)
    settings = await BotSettingsService(session).get_all()
    user_lang = user.language if user and user.language else None
    return get_lang(settings, user_lang)


@router.callback_query(F.data == "buy")
async def show_plans(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        plans = await PlanService(session).get_all(only_active=True)
        photo = await BotSettingsService(session).get("photo_buy")
        lang = await _get_user_lang(callback.from_user.id, session)

    from app.bot.utils.media import edit_with_photo

    if not plans:
        async with AsyncSessionFactory() as session:
            kb = await _get_menu_kb(
                session,
                lang=lang,
                user_id=callback.from_user.id,
                is_admin=_is_admin(callback.from_user.id),
            )
        await edit_with_photo(callback, t("no_plans", lang), reply_markup=kb)
        await callback.answer()
        return

    await edit_with_photo(
        callback,
        t("choose_plan", lang),
        reply_markup=plans_kb(plans, lang=lang),
        photo=photo or None,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery) -> None:
    plan_id = int(callback.data.split(":")[1])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        user = await UserService(session).get_by_id(callback.from_user.id)
        settings = await BotSettingsService(session).get_all()
        svc = BotSettingsService(session)
        user_lang = user.language if user and str(user.language) else None
        lang = get_lang(settings, str(user_lang))
        user_balance = float(user.balance or 0) if user else 0.0
        plan_is_active = plan.is_active if plan else False
        plan_price = float(plan.price) if plan else 0.0
        plan_name = plan.name if plan else ""

        _yk_toggle = (await svc.get("ps_yookassa_enabled") or "0") == "1"
        _sbp_toggle = (await svc.get("ps_sbp_enabled") or "0") == "1"
        _yk_shop_db = await svc.get("yookassa_shop_id_override") or ""
        _yk_key_db = bool(await svc.get("yookassa_secret_key_override"))
        _stars_rate = float(await svc.get("stars_rate") or "1.5")
        _yk_configured = bool(_yk_shop_db and _yk_key_db)
        has_yookassa = _yk_toggle and _yk_configured
        has_sbp = _sbp_toggle and _yk_configured

        has_cryptobot = (
            bool(settings.get("cryptobot_token", "").strip())
            and (await svc.get("ps_cryptobot_enabled") or "0") == "1"
        )

        _fk_toggle = (await svc.get("ps_freekassa_enabled") or "0") == "1"
        _fk_shop = await svc.get("freekassa_shop_id") or ""
        _fk_key = await svc.get("freekassa_api_key") or ""
        has_freekassa = _fk_toggle and bool(_fk_shop and _fk_key)

        _pl_toggle = (await svc.get("ps_platega_enabled") or "0") == "1"
        _pl_merchant = await svc.get("platega_merchant_id") or ""
        _pl_secret = await svc.get("platega_secret") or ""
        has_platega = _pl_toggle and bool(_pl_merchant and _pl_secret)

    if not plan or not plan_is_active:
        await callback.answer(t("no_plans", lang), show_alert=True)
        return

    from app.services.telegram_stars import TelegramStarsService

    stars = TelegramStarsService.rub_to_stars(plan_price, rate=_stars_rate)

    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback,
        t("choose_payment", lang, plan_name=plan_name, price=plan_price),
        reply_markup=payment_methods_kb(
            plan_id,
            stars_amount=stars,
            user_balance=user_balance,
            plan_price=plan_price,
            has_cryptobot=has_cryptobot,
            has_yookassa=has_yookassa,
            has_sbp=has_sbp,
            has_freekassa=has_freekassa,
            has_platega=has_platega,
            lang=lang,
        ),
    )
    await callback.answer()
