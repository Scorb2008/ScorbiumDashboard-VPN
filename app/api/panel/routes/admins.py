"""Admin management routes."""

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.admin import AdminService
from typing import Optional
from .shared import _require_permission, _get_admin_info, _toast, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admins_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "admins")
    ctx["admins"] = await AdminService(db).get_all()
    return templates.TemplateResponse("admins.html", ctx)


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_admin(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("operator"),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    if role not in ("superadmin", "manager", "operator"):
        resp = Response(status_code=400)
        _toast(resp, "Недопустимая роль", "error")
        return resp
    existing = await AdminService(db).get_by_username(username)
    if existing:
        resp = Response(status_code=400)
        _toast(resp, "Администратор с таким именем уже существует", "error")
        return resp
    await AdminService(db).create(username=username, password=password, role=role)
    await db.commit()
    admins = await AdminService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/admins_table.html",
        {"request": request, "admins": admins},
    )
    _toast(resp, f"Администратор {username} создан")
    return resp


@router.post("/{admin_id}/edit", response_class=HTMLResponse)
async def edit_admin(
    admin_id: int,
    request: Request,
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    admin_info = _get_admin_info(request)
    target = await AdminService(db).get_by_id(admin_id)
    if not target:
        resp = Response(status_code=404)
        _toast(resp, "Администратор не найден", "error")
        return resp
    if (
        admin_info
        and target.username == admin_info["sub"]
        and role
        and role != "superadmin"
    ):
        resp = Response(status_code=400)
        _toast(resp, "Нельзя понизить самого себя", "error")
        return resp
    updates = {}
    if username is not None:
        updates["username"] = username
    if password is not None and password.strip():
        updates["password"] = password
    if role is not None:
        updates["role"] = role
    if is_active is not None:
        updates["is_active"] = is_active == "1"
    await AdminService(db).update(admin_id, **updates)
    if password is not None and password.strip():
        from app.services.token_blacklist import TokenBlacklistService

        await TokenBlacklistService(db).blacklist_all_for_user(target.username)
    await db.commit()
    admins = await AdminService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/admins_table.html",
        {"request": request, "admins": admins},
    )
    _toast(resp, "Администратор обновлён")
    return resp


@router.delete("/{admin_id}", response_class=HTMLResponse)
async def delete_admin(
    admin_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "system")
    admin_info = _get_admin_info(request)
    target = await AdminService(db).get_by_id(admin_id)
    if not target:
        resp = Response(status_code=404)
        _toast(resp, "Администратор не найден", "error")
        return resp
    if admin_info and target.username == admin_info["sub"]:
        resp = Response(status_code=400)
        _toast(resp, "Нельзя удалить самого себя", "error")
        return resp
    await AdminService(db).delete(admin_id)
    await db.commit()
    admins = await AdminService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/admins_table.html",
        {"request": request, "admins": admins},
    )
    _toast(resp, "Администратор удалён")
    return resp
