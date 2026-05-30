from app.core.config import config
from app.core.database import AsyncSessionFactory
from app.services.telegram_notify import TelegramNotifyService
from app.services.user import UserService
from app.utils.html_utils import escape_html, html_code
from app.utils.log import log


async def _user_label(user_id: int) -> tuple[str, str]:
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(user_id)

    if not user:
        return "—", html_code(user_id)

    full_name = escape_html(user.full_name or "—")
    username = escape_html(f"@{user.username}") if user.username else html_code(user_id)
    return full_name, username


async def _notify_admins(text: str) -> None:
    notify = TelegramNotifyService()
    for admin_id in config.telegram.telegram_admin_ids:
        try:
            await notify.send_message(admin_id, text)
        except Exception as e:
            log.warning(f"Failed to notify admin {admin_id}: {e}")


async def notify_admins_new_purchase(
    *,
    user_id: int,
    payment_id: int,
    plan_name: str,
    amount: str,
    currency: str,
    provider: str,
    plan_days: int,
    key_issued: bool,
) -> None:
    full_name, username = await _user_label(user_id)
    provider_icons = {
        "yookassa": "💳",
        "yookassa_sbp": "🏦",
        "telegram_stars": "⭐",
        "cryptobot": "₿",
        "freekassa": "🟢",
        "platega": "🟦",
        "aikassa": "🟧",
        "paypalych": "🟨",
        "balance": "💰",
    }
    icon = provider_icons.get(str(provider).lower(), "💰")
    text = (
        "✅ <b>Новая покупка!</b>\n\n"
        f"🧾 Платёж: <code>{payment_id}</code>\n"
        f"👤 {full_name} ({username})\n"
        f"📦 {escape_html(plan_name)} — {amount} {currency}\n"
        f"⏱ {plan_days} дн.\n"
        f"{icon} {escape_html(provider)}\n"
        f"🔑 Ключ: {'выдан' if key_issued else '❌ ошибка'}"
    )
    await _notify_admins(text)


async def notify_admins_balance_topup(
    *,
    user_id: int,
    payment_id: int,
    amount: str,
    balance: str,
    currency: str = "RUB",
    provider: str = "topup",
) -> None:
    full_name, username = await _user_label(user_id)
    text = (
        "💰 <b>Пополнение баланса</b>\n\n"
        f"🧾 Платёж: <code>{payment_id}</code>\n"
        f"👤 {full_name} ({username})\n"
        f"💸 Сумма: <b>{amount} {currency}</b>\n"
        f"👛 Новый баланс: <b>{balance} {currency}</b>\n"
        f"🏦 Провайдер: {escape_html(provider)}"
    )
    await _notify_admins(text)


async def notify_admins_autorenew_success(
    *,
    user_id: int,
    key_id: int,
    key_name: str,
    amount: str,
    expires_at: str,
    plan_days: int,
) -> None:
    full_name, username = await _user_label(user_id)
    text = (
        "🔄 <b>Автопродление выполнено</b>\n\n"
        f"👤 {full_name} ({username})\n"
        f"🔑 Ключ: <code>{key_id}</code>\n"
        f"📦 {escape_html(key_name)}\n"
        f"💸 Списано: <b>{amount} ₽</b>\n"
        f"⏱ Продлено на: <b>{plan_days} дн.</b>\n"
        f"📅 Действует до: <b>{expires_at}</b>"
    )
    await _notify_admins(text)
