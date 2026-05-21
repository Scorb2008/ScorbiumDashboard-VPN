"""Payments management routes."""
from datetime import datetime, timedelta, timezone
from typing import Optional
import html

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.models.payment import Payment,PaymentStatus

from app.services.payment import PaymentService

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()


def _parse_payment_status(value: Optional[str]) -> Optional[PaymentStatus]:
    if not value:
        return None
    try:
        return PaymentStatus(value)
    except ValueError:
        return None


def _render_payments_rows(request: Request, payments):
    return templates.TemplateResponse(
        "partials/payments_rows.html",
        {"request": request, "payments": payments},
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def payments_page(
    request: Request,
    status: Optional[str] = None,
    payment_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "payments")
    ctx = await _base_ctx(request, db, "payments")
    from app.models.payment import PaymentType as PT
    ps = _parse_payment_status(status)
    try:
        pt = PT(payment_type) if payment_type else None
    except ValueError:
        pt = None
    ctx["payments"] = await PaymentService(db).get_all(
        limit=200, status=ps, payment_type=pt
    )
    ctx["total_topups"] = await PaymentService(db).total_topups()
    ctx["current_status"] = status or ""
    ctx["current_type"] = payment_type or ""
    if request.headers.get("HX-Request") == "true":
        return _render_payments_rows(request, ctx["payments"])
    return templates.TemplateResponse("payments.html", ctx)


@router.get("/stats", response_class=HTMLResponse)
async def payments_stats_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "payments")
    ctx = await _base_ctx(request, db, "payments_stats")
    days = 30
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount), 0).label('total'),
            func.count(Payment.id).label('count'),
            func.avg(Payment.amount).label('avg'),
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        )
    )
    row = result.first()
    total_rev = float(row.total) if row else 0
    total_pay = row.count if row else 0
    avg_pay = float(row.avg) if row and row.avg else 0

    result = await db.execute(
        select(func.count(Payment.id)).where(Payment.created_at >= cutoff)
    )
    all_count = result.scalar() or 1

    result = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        )
    )
    success_count = result.scalar() or 0

    ctx["stats"] = {
        "total_revenue": f"{total_rev:.2f}",
        "total_payments": total_pay,
        "avg_payment": f"{avg_pay:.2f}",
        "success_rate": round(success_count / all_count * 100) if all_count else 0,
    }

    provider_names = {
        'yookassa': 'YooKassa', 'yookassa_sbp': 'YooKassa СБП', 'cryptobot': 'CryptoBot',
        'telegram_stars': 'Telegram Stars', 'freekassa': 'FreeKassa', 'balance': 'Баланс', 'topup': 'Пополнение'
    }
    result = await db.execute(
        select(
            Payment.provider,
            func.count(Payment.id).label('cnt'),
            func.coalesce(func.sum(Payment.amount).filter(Payment.status == PaymentStatus.SUCCEEDED.value), 0).label('rev'),
            func.count(Payment.id).filter(Payment.status == PaymentStatus.SUCCEEDED.value).label('scnt'),
        ).where(
            Payment.created_at >= cutoff
        ).group_by(Payment.provider)
    )
    providers = []
    for r in result.all():
        providers.append({
            "provider": r.provider,
            "label": provider_names.get(r.provider, r.provider),
            "count": r.cnt,
            "revenue": float(r.rev),
            "success_count": r.scnt,
            "avg_amount": float(r.rev) / r.scnt if r.scnt else 0,
        })
    total_prov = sum(p["count"] for p in providers) or 1
    for p in providers:
        p["share"] = round(p["count"] / total_prov * 100)
    ctx["providers"] = sorted(providers, key=lambda x: x["revenue"], reverse=True)

    return templates.TemplateResponse("payments_stats.html", ctx)


@router.get("/stats/json")
async def payments_stats_json(request: Request, days: int = 30, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "payments")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount), 0).label('total'),
            func.count(Payment.id).label('count'),
            func.avg(Payment.amount).label('avg'),
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        )
    )
    row = result.first()
    total_rev = float(row.total) if row else 0
    total_pay = row.count if row else 0
    avg_pay = float(row.avg) if row and row.avg else 0

    result = await db.execute(select(func.count(Payment.id)).where(Payment.created_at >= cutoff))
    all_count = result.scalar() or 1
    result = await db.execute(select(func.count(Payment.id)).where(
        Payment.status == PaymentStatus.SUCCEEDED.value, Payment.created_at >= cutoff))
    success_count = result.scalar() or 0

    provider_names = {
        'yookassa': 'YooKassa', 'yookassa_sbp': 'YooKassa СБП', 'cryptobot': 'CryptoBot',
        'telegram_stars': 'Telegram Stars', 'freekassa': 'FreeKassa', 'balance': 'Баланс', 'topup': 'Пополнение'
    }
    provider_icons = {
        'yookassa': 'credit-card', 'yookassa_sbp': 'bank', 'cryptobot': 'currency-bitcoin',
        'telegram_stars': 'star', 'freekassa': 'lightning', 'balance': 'wallet2', 'topup': 'plus-circle'
    }
    result = await db.execute(
        select(
            Payment.provider,
            func.count(Payment.id).label('cnt'),
            func.coalesce(func.sum(Payment.amount).filter(Payment.status == PaymentStatus.SUCCEEDED.value), 0).label('rev'),
            func.count(Payment.id).filter(Payment.status == PaymentStatus.SUCCEEDED.value).label('scnt'),
        ).where(Payment.created_at >= cutoff).group_by(Payment.provider)
    )
    providers = []
    for r in result.all():
        providers.append({
            "provider": r.provider,
            "label": provider_names.get(r.provider, r.provider),
            "icon": provider_icons.get(r.provider, 'question-circle'),
            "count": r.cnt,
            "revenue": float(r.rev),
            "success_count": r.scnt,
            "avg_amount": float(r.rev) / r.scnt if r.scnt else 0,
        })
    total_prov = sum(p["count"] for p in providers) or 1
    for p in providers:
        p["share"] = round(p["count"] / total_prov * 100)

    result = await db.execute(
        select(
            func.date_trunc('day', Payment.created_at).label('day'),
            func.coalesce(func.sum(Payment.amount), 0).label('amount'),
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        ).group_by(func.date_trunc('day', Payment.created_at)).order_by(func.date_trunc('day', Payment.created_at))
    )
    daily = [{"date": str(r.day)[:10], "amount": float(r.amount)} for r in result.all()]

    return JSONResponse({
        "total_revenue": f"{total_rev:.2f}",
        "total_payments": total_pay,
        "avg_payment": f"{avg_pay:.2f}",
        "success_rate": round(success_count / all_count * 100) if all_count else 0,
        "providers": sorted(providers, key=lambda x: x["revenue"], reverse=True),
        "daily": daily,
    })


@router.post("/{payment_id}/refund", response_class=HTMLResponse)
async def refund_payment_view(
    payment_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "payments")
    payment = await PaymentService(db).refund(payment_id)
    if not payment:
        resp = Response(status_code=404)
        _toast(resp, 'Платёж не найден', 'error')
        return resp
    h = html.escape
    resp = HTMLResponse(f"""<tr>
      <td><code style="color:#00d4aa">#{payment.id}</code></td>
      <td><a href="/panel/users/{payment.user_id}" style="color:#00d4aa">{payment.user_id}</a></td>
      <td><span style="color:#8892a4;font-size:.8rem">{h(str(payment.provider))}</span></td>
      <td><b>{payment.amount}</b> {h(str(payment.currency))}</td>
      <td><span class="badge badge-custom badge-open">Возврат</span></td>
      <td style="color:#8892a4;font-size:.8rem">—</td><td></td></tr>""")
    _toast(resp, f"Возврат платежа #{payment_id} выполнен")
    return resp
