"""Promo code management routes."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.promo import PromoService

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def promos_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "promos")
    ctx = await _base_ctx(request, db, "promos")
    ctx["promos"] = await PromoService(db).get_all()
    return templates.TemplateResponse("promos.html", ctx)


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_promo(
    request: Request,
    code: str = Form(...),
    discount_percent: int = Form(...),
    max_uses: int = Form(0),
    expires_days: int = Form(30),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "promos")
    if discount_percent < 1 or discount_percent > 100:
        resp = Response(status_code=400)
        _toast(resp, "Скидка должна быть от 1 до 100%", "error")
        return resp
    await PromoService(db).create(
        code=code.strip(),
        promo_type="discount_percent",
        value=discount_percent,
        max_uses=max_uses or None,
        expires_days=expires_days,
    )
    await db.commit()
    resp = templates.TemplateResponse(
        "partials/promos_list.html",
        {"request": request, "promos": await PromoService(db).get_all()},
    )
    _toast(resp, f"Промокод {code} создан")
    return resp


@router.delete("/{promo_id}", response_class=HTMLResponse)
async def delete_promo(
    promo_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "promos")
    await PromoService(db).delete(promo_id)
    resp = HTMLResponse("")
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
    status_text = "активен" if promo.is_active else "отключён"
    resp = HTMLResponse(f"""<div id="promo-{promo.id}" style="opacity:.6">
        <span>{promo.code}</span> — {status_text}
    </div>""")
    _toast(resp, f"Промокод {status_text}")
    return resp
