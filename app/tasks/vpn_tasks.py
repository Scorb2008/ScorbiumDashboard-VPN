import asyncio

from app.core.database import AsyncSessionFactory
from app.services.vpn_key import VpnKeyService
from app.utils.log import log

EXPIRE_CHECK_INTERVAL = 300 
SYNC_INTERVAL = 3600 


async def expire_outdated_keys() -> None:
    """Mark expired VPN keys and disable them in Marzban."""
    try:
        async with AsyncSessionFactory() as session:
            count = await VpnKeyService(session).expire_outdated()
            await session.commit()
            if count:
                log.info(f"[vpn_tasks] Expired {count} outdated VPN keys")
                from app.services.notification import notification_manager
                await notification_manager.broadcast({
                    "type": "expired_sub",
                    "data": {"count": count},
                })
    except Exception as e:
        log.error(f"[vpn_tasks] expire_outdated_keys error: {e}")


async def sync_keys_from_marzban() -> None:
    """Sync VPN key statuses from the configured VPN panel."""
    try:
        async with AsyncSessionFactory() as session:
            result = await VpnKeyService(session).sync_from_marzban()
            await session.commit()
            log.info(f"[vpn_tasks] VPN panel sync: {result}")
    except Exception as e:
        log.error(f"[vpn_tasks] sync_keys_from_marzban error: {e}")


async def expire_loop() -> None:
    log.info("⏰ VPN expiry task started")
    while True:
        try:
            await asyncio.sleep(EXPIRE_CHECK_INTERVAL)
            await expire_outdated_keys()
            await notify_expiring_soon()
            await auto_renew_keys()
        except Exception as e:
            log.error("expire_loop error: %s", e, exc_info=True)
            await asyncio.sleep(EXPIRE_CHECK_INTERVAL)


async def sync_loop() -> None:
    log.info("🔄 Marzban sync task started")
    await asyncio.sleep(60)
    while True:
        try:
            await sync_keys_from_marzban()
            await asyncio.sleep(SYNC_INTERVAL)
        except Exception as e:
            log.error("sync_loop error: %s", e, exc_info=True)
            await asyncio.sleep(SYNC_INTERVAL)


async def notify_expiring_soon() -> None:
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from app.models.user import User
    from app.services.telegram_notify import TelegramNotifyService
    from app.services.bot_settings import BotSettingsService
    from app.services.i18n import get_lang

    try:
        async with AsyncSessionFactory() as session:
            settings = await BotSettingsService(session).get_all()
            photo = await BotSettingsService(session).get("photo_status") or None

        if settings.get("notify_expiry_enabled", "1") != "1":
            return

        raw_days = settings.get("notify_expiry_days", "7,3,1")
        notify_days = []
        for d in raw_days.split(","):
            try:
                notify_days.append(int(d.strip()))
            except ValueError:
                pass
        if not notify_days:
            return

        notify_msg_tpl = settings.get(
            "notify_expiry_message",
            "⚠️ <b>Подписка истекает через {days} дн.!</b>\n\n📦 {name}\n📅 Дата истечения: <b>{date}</b>\n\nПродлите подписку чтобы не потерять доступ.",
        )

        now = datetime.now(timezone.utc)
        notify = TelegramNotifyService()
        total_sent = 0

        all_keys = []
        for days_before in notify_days:
            window_start = now + timedelta(days=days_before) - timedelta(minutes=5)
            window_end = now + timedelta(days=days_before) + timedelta(minutes=5)

            async with AsyncSessionFactory() as session:
                result = await session.execute(
                    select(VpnKey).where(
                        VpnKey.status == VpnKeyStatus.ACTIVE.value,
                        VpnKey.expires_at >= window_start,
                        VpnKey.expires_at <= window_end,
                    )
                )
                for k in result.scalars().all():
                    all_keys.append((k.user_id, k.name or f"Подписка #{k.id}", k.expires_at, days_before))

        if not all_keys:
            return
        
        user_ids = list({k[0] for k in all_keys})
        async with AsyncSessionFactory() as session:
            user_result = await session.execute(
                select(User).where(User.id.in_(user_ids))
            )
            users = {u.id: u for u in user_result.scalars().all()}

        for user_id, name, exp, days_before in all_keys:
            u = users.get(user_id)
            if not u or u.is_banned:
                continue

            user_lang = u.language if u.language else None
            lang = get_lang(settings, user_lang)

            exp_str = exp.strftime("%d.%m.%Y") if exp else "—"

            if lang == "en":
                msg = (
                    f"⚠️ <b>Subscription expires in {days_before} day(s)!</b>\n\n"
                    f"📦 {name}\n📅 Expiry date: <b>{exp_str}</b>\n\n"
                    f"Renew your subscription to keep VPN access."
                )
            elif lang == "fa":
                msg = (
                    f"⚠️ <b>اشتراک شما در {days_before} روز منقضی می‌شود!</b>\n\n"
                    f"📦 {name}\n📅 تاریخ انقضا: <b>{exp_str}</b>\n\n"
                    f"اشتراک خود را تمدید کنید."
                )
            else:
                try:
                    msg = notify_msg_tpl.format(days=days_before, name=name, date=exp_str)
                except Exception:
                    msg = f"⚠️ Подписка «{name}» истекает через {days_before} дн. ({exp_str})"

            if photo:
                await notify.send_photo(user_id, photo, msg)
            else:
                await notify.send_message(user_id, msg)
            total_sent += 1

        if total_sent:
            log.info("[vpn_tasks] Sent %d expiry notifications", total_sent)

    except Exception as e:
        log.error("[vpn_tasks] notify_expiring_soon error: %s", e, exc_info=True)


async def auto_renew_keys() -> None:
    """Auto-renew expired keys for users with autorenew enabled and sufficient balance."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from app.services.user import UserService
    from app.services.vpn_key import VpnKeyService
    from app.services.telegram_notify import TelegramNotifyService
    from app.services.bot_settings import BotSettingsService
    from decimal import Decimal

    now = datetime.now(timezone.utc)
    expired_since = now - timedelta(hours=1)

    try:

        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(VpnKey).where(
                    VpnKey.expires_at >= expired_since,
                    VpnKey.expires_at <= now,
                    VpnKey.price.isnot(None),
                    VpnKey.plan_id.isnot(None),
                )
            )
            keys = list(result.scalars().all())
            data = [
                {
                    "key_id": k.id,
                    "user_id": k.user_id,
                    "plan_id": k.plan_id,
                    "price": float(k.price or 0),
                    "name": k.name or f"Подписка #{k.id}",
                }
                for k in keys
            ]
            photo = await BotSettingsService(session).get("photo_status") or None

        notify = TelegramNotifyService()
        
        for item in data:
            key_id = item["key_id"]
            user_id = item["user_id"]
            plan_id = item["plan_id"]
            price = item["price"]
            name = item["name"]
            
            if price <= 0:
                continue
                
            try:
                async with AsyncSessionFactory() as session:
                    from sqlalchemy import select as _select
                    from app.models.vpn_key import VpnKey as _VpnKey

                    key_result = await session.execute(
                        _select(_VpnKey)
                        .where(_VpnKey.id == key_id)
                        .with_for_update()
                    )
                    current_key = key_result.scalar_one_or_none()
                    if not current_key or current_key.expires_at > now:
                        continue 

                    user_check = await UserService(session).get_by_id(user_id)
                    if not user_check or not bool(user_check.autorenew):
                        continue

                    user = await UserService(session).deduct_balance(user_id, Decimal(str(price)))
                    if not user:

                        await notify.send_message(
                            user_id,
                            "⚠️ <b>Автопродление не выполнено</b>\n\n"
                            "Недостаточно средств на балансе. Пополните баланс для продления подписки.",
                        )
                        continue

                    from app.services.plan import PlanService
                    plan = await PlanService(session).get_by_id(plan_id)
                    if not plan:
                        await UserService(session).add_balance(user_id, Decimal(str(price)))
                        await session.commit()
                        log.warning(f"[auto_renew] Plan {plan_id} not found, refunded {price} to user {user_id}")
                        continue

                    key = await VpnKeyService(session).extend(key_id, plan.duration_days)
                    
                    if key:
                        
                        exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                        key_id_for_log = key.id
                        await session.commit()
                        log.info(f"[auto_renew] key={key_id_for_log} user={user_id} renewed")
                        renew_msg = (
                            f"✅ <b>Подписка автоматически продлена!</b>\n\n"
                            f"📦 {name}\n"
                            f"Списано: <b>{price} ₽</b>\n"
                            f"Действует до: <b>{exp_str}</b>"
                        )
                        if photo:
                            await notify.send_photo(user_id, photo, renew_msg)
                        else:
                            await notify.send_message(user_id, renew_msg)
                        log.info(f"[auto_renew] key={key_id} user={user_id} renewed for {plan.duration_days} days")
                    else:
                        await UserService(session).add_balance(user_id, Decimal(str(price)))
                        await session.commit()
                        log.error(f"[auto_renew] Extension failed for key={key_id}, refunded")
                        
            except Exception as e:
                log.error(f"[auto_renew] key={key_id} user={user_id} error: {e}")
                
    except Exception as e:
        log.error(f"[auto_renew] error: {e}")
