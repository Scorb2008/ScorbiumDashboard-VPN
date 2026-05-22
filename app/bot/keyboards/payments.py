from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models.plan import Plan


def plans_kb(plans: list[Plan], lang: str = "ru") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        label = f"{plan.name} — {plan.price} ₽"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"plan:{plan.id}"))
    back_text = "◀️ Назад" if lang == "ru" else "◀️ Back"
    builder.row(InlineKeyboardButton(text=back_text, callback_data="back_main"))
    return builder.as_markup()


def payment_methods_kb(
    plan_id: int,
    stars_amount: int = 0,
    user_balance: float = 0.0,
    plan_price: float = 0.0,
    has_cryptobot: bool = False,
    has_yookassa: bool = False,
    has_sbp: bool = False,
    has_freekassa: bool = False,
    has_platega: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # ЮКасса (карта)
    if has_yookassa:
        card_labels = {
            "ru": "💳 Банковская карта",
            "en": "💳 Bank card",
            "fa": "💳 کارت بانکی",
        }
        builder.row(
            InlineKeyboardButton(
                text=card_labels.get(lang, card_labels["ru"]),
                callback_data=f"pay:yookassa:{plan_id}",
            )
        )

    # СБП — отдельный флаг
    if has_sbp:
        sbp_labels = {"ru": "🏦 СБП", "en": "🏦 SBP", "fa": "🏦 پرداخت سریع"}
        builder.row(
            InlineKeyboardButton(
                text=sbp_labels.get(lang, sbp_labels["ru"]),
                callback_data=f"pay:sbp:{plan_id}",
            )
        )

    # FreeKassa
    if has_freekassa:
        fk_labels = {
            "ru": "💸 FreeKassa (карта/СБП)",
            "en": "💸 FreeKassa",
            "fa": "💸 FreeKassa",
        }
        builder.row(
            InlineKeyboardButton(
                text=fk_labels.get(lang, fk_labels["ru"]),
                callback_data=f"pay:freekassa:{plan_id}",
            )
        )

    if has_platega:
        platega_labels = {"ru": "🟦 Platega", "en": "🟦 Platega", "fa": "🟦 Platega"}
        builder.row(
            InlineKeyboardButton(
                text=platega_labels.get(lang, platega_labels["ru"]),
                callback_data=f"pay:platega:{plan_id}",
            )
        )

    # Telegram Stars — всегда
    stars_label = (
        f"⭐ Telegram Stars ({stars_amount} ⭐)"
        if stars_amount
        else "⭐ Telegram Stars"
    )
    builder.row(
        InlineKeyboardButton(text=stars_label, callback_data=f"pay:stars:{plan_id}")
    )

    # CryptoBot
    if has_cryptobot:
        crypto_labels = {
            "ru": "₿ Криптовалюта (CryptoBot)",
            "en": "₿ Cryptocurrency (CryptoBot)",
            "fa": "₿ ارز دیجیتال",
        }
        builder.row(
            InlineKeyboardButton(
                text=crypto_labels.get(lang, crypto_labels["ru"]),
                callback_data=f"pay:crypto:{plan_id}",
            )
        )

    # Баланс
    if user_balance > 0 and user_balance >= plan_price:
        bal_labels = {
            "ru": f"💰 Оплатить с баланса ({user_balance:.2f} ₽)",
            "en": f"💰 Pay from balance ({user_balance:.2f} ₽)",
            "fa": f"💰 پرداخت از موجودی ({user_balance:.2f} ₽)",
        }
        builder.row(
            InlineKeyboardButton(
                text=bal_labels.get(lang, bal_labels["ru"]),
                callback_data=f"pay:balance:{plan_id}",
            )
        )

    back_labels = {"ru": "◀️ Назад", "en": "◀️ Back", "fa": "◀️ بازگشت"}
    builder.row(
        InlineKeyboardButton(text=back_labels.get(lang, "◀️ Назад"), callback_data="buy")
    )
    return builder.as_markup()
