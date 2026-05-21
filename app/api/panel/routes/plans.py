"""Plans management routes."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.plan import PlanService

from .shared import _base_ctx, _require_permission, _toast, templates

router = APIRouter()


def _render_plans_grid(request: Request, plans):
    return templates.TemplateResponse(
        "partials/plans_grid.html", {"request": request, "plans": plans}
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def plans_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "plans")
    ctx = await _base_ctx(request, db, "plans")
    ctx["plans"] = await PlanService(db).get_all()
    return templates.TemplateResponse("plans.html", ctx)


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_plan_view(
    request: Request,
    name: str = Form(...),
    price: Decimal = Form(...),
    duration_days: int = Form(...),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "plans")
    if price <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Цена должна быть больше нуля", "error")
        return resp
    if duration_days < 1:
        resp = Response(status_code=400)
        _toast(resp, "Длительность должна быть минимум 1 день", "error")
        return resp
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_") or "plan"
    existing = await PlanService(db).get_by_slug(slug)
    if existing:
        import time
        slug = f"{slug}_{int(time.time()) % 10000}"
    await PlanService(db).create(
        name=name,
        slug=slug,
        duration_days=duration_days,
        price=price,
        description=description or None,
    )
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = _render_plans_grid(request, plans)
    _toast(resp, f"Тариф «{name}» создан")
    return resp


@router.post("/{plan_id}/edit", response_class=HTMLResponse)
async def edit_plan_view(
    plan_id: int,
    request: Request,
    name: str = Form(...),
    price: Decimal = Form(...),
    duration_days: int = Form(...),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "plans")
    if price <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Цена должна быть больше нуля", "error")
        return resp
    if duration_days < 1:
        resp = Response(status_code=400)
        _toast(resp, "Длительность должна быть минимум 1 день", "error")
        return resp
    plan = await PlanService(db).update(
        plan_id,
        name=name,
        price=price,
        duration_days=duration_days,
        description=description or None,
    )
    if not plan:
        resp = Response(status_code=404)
        _toast(resp, "Тариф не найден", "error")
        return resp
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = _render_plans_grid(request, plans)
    _toast(resp, f"Тариф «{plan.name if plan else plan_id}» обновлён")
    return resp


@router.post("/{plan_id}/toggle", response_class=HTMLResponse)
async def toggle_plan_view(
    plan_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "plans")
    plan = await PlanService(db).toggle_active(plan_id)
    if not plan:
        resp = Response(status_code=404)
        _toast(resp, 'Тариф не найден', 'error')
        return resp
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = _render_plans_grid(request, plans)
    _toast(resp, f"Тариф {'включён' if plan.is_active else 'отключён'}")
    return resp


@router.delete("/{plan_id}", response_class=HTMLResponse)
async def delete_plan_view(
    plan_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "plans")
    deleted = await PlanService(db).delete(plan_id)
    if not deleted:
        resp = Response(status_code=404)
        _toast(resp, "Тариф не найден", "error")
        return resp
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = _render_plans_grid(request, plans)
    _toast(resp, "Тариф удалён")
    return resp


@router.post("/reorder")
async def reorder_plans(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "plans")
    body = await request.json()
    order = body.get("order", [])
    for idx, plan_id_str in enumerate(order):
        try:
            plan_id = int(plan_id_str)
            await PlanService(db).update(plan_id, sort_order=idx)
        except (ValueError, Exception):
            pass
    await db.commit()
    return JSONResponse({"ok": True})
