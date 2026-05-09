"""Dashboard routes."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select, cast, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.api.dependencies import get_db
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.user import User
from app.models.vpn_key import VpnKey, VpnKeyStatus
from app.services.bot_settings import BotSettingsService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService

from .shared import _require_permission, _base_ctx, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "dashboard")
    ctx = await _base_ctx(request, db, "dashboard", admin_info)

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    new_today_r = await db.execute(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    )
    new_today = new_today_r.scalar_one()

    rev_today_r = await db.execute(
        select(
            func.coalesce(func.sum(cast(Payment.amount, Numeric)), 0).label("total")
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.payment_type == PaymentType.SUBSCRIPTION.value,
            Payment.created_at >= today_start,
        )
    )
    rev_today_val = rev_today_r.scalar_one()
    rev_today = float(rev_today_val) if rev_today_val else 0.0

    expired_r = await db.execute(
        select(func.count())
        .select_from(VpnKey)
        .where(VpnKey.status == VpnKeyStatus.EXPIRED.value)
    )
    expired_count = expired_r.scalar_one()

    rev_week = []
    for i in range(6, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        r = await db.execute(
            select(
                func.coalesce(func.sum(cast(Payment.amount, Numeric)), 0).label("total")
            ).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.payment_type == PaymentType.SUBSCRIPTION.value,
                Payment.created_at >= day_start,
                Payment.created_at < day_end,
            )
        )
        val = r.scalar_one()
        rev_week.append(float(val) if val else 0.0)

    from app.services.support import SupportService
    ctx["stats"] = {
        "total_users": await UserService(db).count_all(),
        "active_subscriptions": await VpnKeyService(db).count_active(),
        "total_revenue": await PaymentService(db).total_revenue(),
        "total_topups": await PaymentService(db).total_topups(),
        "open_tickets": await SupportService(db).count_open(),
        "new_users_today": new_today,
        "revenue_today": rev_today,
        "expired_keys": expired_count,
        "pending_payments": await PaymentService(db).count_by_status(
            PaymentStatus.PENDING
        ),
    }
    ctx["rev_week"] = rev_week
    ctx["recent_users"] = await UserService(db).get_all(limit=8)
    ctx["recent_payments"] = await PaymentService(db).get_all(limit=8)

    from app.services.pasarguard.pasarguard import get_vpn_panel

    try:
        panel_stats = await get_vpn_panel().get_system_stats()
        ctx["marzban_stats"] = panel_stats
    except Exception:
        ctx["marzban_stats"] = None

    try:
        from app.services.system_metrics import SystemMetrics
        ctx["system_metrics"] = await SystemMetrics.collect()
    except Exception:
        ctx["system_metrics"] = None

    return templates.TemplateResponse("dashboard.html", ctx)
