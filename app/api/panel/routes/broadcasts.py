"""Broadcast messages routes."""
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.broadcast import BroadcastService

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def broadcasts_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "broadcasts")
    ctx = await _base_ctx(request, db, "broadcasts")
    ctx["broadcasts"] = await BroadcastService(db).get_all()
    return templates.TemplateResponse("broadcasts.html", ctx)


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_broadcast_view(
    request: Request,
    title: str = Form(...),
    text: str = Form(...),
    target: str = Form("all"),
    parse_mode: str = Form("HTML"),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "broadcasts")
    await BroadcastService(db).create(
        title=title, text=text, target=target, parse_mode=parse_mode
    )
    resp = templates.TemplateResponse(
        "partials/broadcasts_list.html",
        {"request": request, "broadcasts": await BroadcastService(db).get_all()},
    )
    _toast(resp, "Черновик создан")
    return resp


@router.post("/{broadcast_id}/send", response_class=HTMLResponse)
async def send_broadcast_view(
    broadcast_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "broadcasts")
    bc = await BroadcastService(db).send(broadcast_id)
    if not bc:
        return HTMLResponse("", status_code=400)
    resp = templates.TemplateResponse(
        "partials/broadcasts_list.html", {"request": request, "broadcasts": [bc]}
    )
    _toast(resp, f"Отправлено: {bc.sent_count}, ошибок: {bc.failed_count}")
    return resp


@router.get("/estimate")
async def broadcast_estimate(request: Request, target: str = "all", db: AsyncSession = Depends(get_db)):
    _require_permission(request, "broadcasts")
    count = await BroadcastService(db).estimate_count(target)
    return JSONResponse({"count": count})
