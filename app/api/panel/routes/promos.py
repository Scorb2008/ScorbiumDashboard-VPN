"""Promo code management routes."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.plan import PlanService
from app.services.promo import PromoService

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()


def _render_promos_table(request: Request, promos):
    return templates.TemplateResponse(
        "partials/promos_table.html",
        {"request": request, "promos": promos},
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def promos_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "promos")
    ctx = await _base_ctx(request, db, "promos")
    ctx["promos"] = await PromoService(db).get_all()
    ctx["plans"] = await PlanService(db).get_all()
    return templates.TemplateResponse("promos.html", ctx)


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_promo(
    request: Request,
    code: str = Form(...),
    promo_type: str = Form("discount"),
    value: Decimal = Form(...),
    plan_id: Optional[int] = Form(None),
    max_uses: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "promos")
    await PromoService(db).create(
        code=code.strip(),
        promo_type=promo_type,
        value=value,
        plan_id=plan_id,
        max_uses=max_uses or 0,
    )
    await db.commit()
    resp = _render_promos_table(request, await PromoService(db).get_all())
    _toast(resp, f"Промокод {code} создан")
    return resp


@router.delete("/{promo_id}", response_class=HTMLResponse)
async def delete_promo(
    promo_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "promos")
    await PromoService(db).delete(promo_id)
    await db.commit()
    resp = _render_promos_table(request, await PromoService(db).get_all())
    _toast(resp, "Промокод удалён")
    return resp


@router.post("/{promo_id}/toggle", response_class=HTMLResponse)
async def toggle_promo(
    promo_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "promos")
    promo = await PromoService(db).toggle_active(promo_id)
    if not promo:
        resp = Response(status_code=404)
        _toast(resp, 'Промокод не найден', 'error')
        return resp
    await db.commit()
    status_text = "активен" if promo.is_active else "отключён"
    resp = _render_promos_table(request, await PromoService(db).get_all())
    _toast(resp, f"Промокод {status_text}")
    return resp
