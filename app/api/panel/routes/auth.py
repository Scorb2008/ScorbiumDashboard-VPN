"""Authentication, 2FA, and Mini App login routes."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.admin import Admin, AdminRole
from app.services.admin import AdminService
from app.services.bot_settings import BotSettingsService
from app.utils.log import log
from app.utils.security import create_access_token, decode_access_token_full
from app.core.permissions import has_permission

from .shared import (
    SESSION_COOKIE, _mini_app_tokens, _time,
    _toast, _get_admin_info, _require_auth, _require_permission,
    _redirect, _base_ctx, _check_session, templates,
)

router = APIRouter()

import secrets as _secrets
import pyotp
import hashlib
import json
import qrcode
import base64
import io


@router.get("/ws-token")
@router.get("/ws-token/")
async def ws_token(request: Request):
    """Return a short-lived token for WebSocket authentication."""
    admin_info = _require_auth(request)
    token = create_access_token(
        subject=admin_info["sub"],
        role=admin_info["role"],
        expires_delta=timedelta(minutes=1),
    )
    return {"token": token}


# ── Mini App auto-login ───────────────────────────────────────────────────────

@router.get("/miniapp-token")
async def get_miniapp_token(request: Request):
    _check_session(request) or _redirect("/panel/login")
    token = _secrets.token_urlsafe(32)
    _mini_app_tokens[token] = _time.time() + 300
    return {"token": token}


@router.get("/miniapp-login")
async def miniapp_login(request: Request, token: str = ""):
    now = _time.time()
    expired = [k for k, v in _mini_app_tokens.items() if v < now]
    for k in expired:
        del _mini_app_tokens[k]

    if not token or token not in _mini_app_tokens or _mini_app_tokens[token] < now:
        return RedirectResponse(url="/panel/login", status_code=302)

    del _mini_app_tokens[token]
    session_token = create_access_token(subject=config.web.web_superadmin_username)
    resp = RedirectResponse(url="/panel/", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE, session_token,
        httponly=True, samesite="none", secure=True, max_age=3600,
    )
    return resp


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
@router.get("/login/", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    settings = await BotSettingsService(db).get_all()
    custom_logo = settings.get("custom_logo", "")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "app_name": config.web.app_name,
            "app_version": config.web.app_version,
            "custom_logo": custom_logo,
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    settings = await BotSettingsService(db).get_all()
    _error_ctx = {
        "request": request,
        "error": "Неверный логин или пароль",
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "custom_logo": settings.get("custom_logo", ""),
    }

    admin = None
    admin = await AdminService(db).authenticate(username, password)
    if admin:
        pass
    elif (
        username == config.web.web_superadmin_username
        and password == config.web.web_superadmin_password.get_secret_value()
    ):
        admin = await AdminService(db).get_by_username(username)
        if not admin:
            import bcrypt as _bcrypt
            new_admin = Admin(
                username=username,
                password_hash=_bcrypt.hashpw(
                    config.web.web_superadmin_password.get_secret_value().encode(),
                    _bcrypt.gensalt()
                ).decode(),
                role=AdminRole.SUPERADMIN.value,
            )
            db.add(new_admin)
            await db.commit()
            await db.refresh(new_admin)
            admin = new_admin

    if not admin:
        # Add delay to prevent timing attacks
        import asyncio
        await asyncio.sleep(1)
        return templates.TemplateResponse("login.html", _error_ctx)

    if not admin.is_active:
        return templates.TemplateResponse("login.html", {**_error_ctx, "error": "Аккаунт заблокирован"})

    token = create_access_token(subject=admin.username, role=admin.role)
    resp = RedirectResponse(url="/panel/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/panel/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    resp.delete_cookie("vpn_preauth")
    return resp


# ── 2FA ─────────────────────────────────────────────────────────────────────

@router.get("/2fa", response_class=HTMLResponse)
async def twofa_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "2fa")
    ctx["admin"] = await AdminService(db).get_by_username(admin_info["sub"])
    return templates.TemplateResponse("two_fa.html", ctx)


@router.post("/2fa-login")
async def twofa_login_submit(
    request: Request, code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select

    settings = await BotSettingsService(db).get_all()
    _error_ctx = {
        "request": request,
        "error": "Неверный код 2FA",
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "custom_logo": settings.get("custom_logo", ""),
        "show_2fa": True,
        "bot_language": settings.get("bot_language", "ru"),
    }

    result = await db.execute(select(Admin).where(Admin.is_active == True, Admin.totp_secret.isnot(None)))
    admins_with_2fa = result.scalars().all()

    for admin in admins_with_2fa:
        totp = pyotp.TOTP(admin.totp_secret, interval=30, digits=6)
        if totp.verify(code):
            token = create_access_token(subject=admin.username, role=admin.role)
            resp = RedirectResponse(url="/panel/", status_code=302)
            resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
            return resp

        if admin.backup_codes:
            try:
                hashed_codes = json.loads(admin.backup_codes)
                code_hash = hashlib.sha256(code.upper().replace("-", "").strip().encode()).hexdigest()
                if code_hash in hashed_codes:
                    hashed_codes.remove(code_hash)
                    admin.backup_codes = json.dumps(hashed_codes)
                    await db.commit()
                    token = create_access_token(subject=admin.username, role=admin.role)
                    resp = RedirectResponse(url="/panel/", status_code=302)
                    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
                    return resp
            except Exception:
                pass

    return templates.TemplateResponse("login.html", _error_ctx)


@router.get("/2fa/setup")
async def twofa_setup(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "system")
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret, interval=30, digits=6)
    uri = totp.provisioning_uri(name=admin_info["sub"], issuer_name=config.web.app_name or "Scorbium")
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return JSONResponse({"secret": secret, "qr_b64": qr_b64})


@router.post("/2fa/activate")
async def twofa_activate(request: Request, db: AsyncSession = Depends(get_db)):
    from app.services.audit import AuditService

    admin_info = _require_permission(request, "system")
    body = await request.json()
    secret = body.get("secret", "")
    code = body.get("code", "")
    if len(code) != 6:
        return JSONResponse({"ok": False, "message": "Код должен быть 6 знаков"}, status_code=400)
    totp = pyotp.TOTP(secret, interval=30, digits=6)
    if not totp.verify(code):
        return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)
    admin = await AdminService(db).get_by_username(admin_info["sub"])
    if admin:
        admin.totp_secret = secret
        raw_codes = [secrets.token_hex(4).upper() for _ in range(8)]
        hashed_codes = [hashlib.sha256(c.encode()).hexdigest() for c in raw_codes]
        admin.backup_codes = json.dumps(hashed_codes)
        await db.commit()
        await AuditService(db).log(admin.id, "2fa_enabled", "admin", admin.id)
        await db.commit()
        return JSONResponse({"ok": True, "message": "2FA активирована", "backup_codes": raw_codes})
    return JSONResponse({"ok": False, "message": "Админ не найден"}, status_code=404)


@router.post("/2fa/verify")
async def twofa_verify(request: Request, db: AsyncSession = Depends(get_db)):
    preauth = request.cookies.get("vpn_preauth", "")
    if not preauth:
        return JSONResponse({"ok": False, "message": "Нет сессии"}, status_code=401)
    try:
        payload = decode_access_token_full(preauth)
        if not payload or payload.get("type") != "preauth":
            return JSONResponse({"ok": False, "message": "Нет сессии"}, status_code=401)
    except Exception:
        return JSONResponse({"ok": False, "message": "Нет сессии"}, status_code=401)

    body = await request.json()
    code = body.get("code", "")
    admin = await AdminService(db).get_by_username(payload["sub"])
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "message": "2FA не настроена"}, status_code=400)

    used_backup = False
    totp = pyotp.TOTP(admin.totp_secret, interval=30, digits=6)
    if not totp.verify(code):
        if admin.backup_codes:
            try:
                hashed_codes = json.loads(admin.backup_codes)
                code_hash = hashlib.sha256(code.upper().replace("-", "").strip().encode()).hexdigest()
                if code_hash in hashed_codes:
                    hashed_codes.remove(code_hash)
                    admin.backup_codes = json.dumps(hashed_codes)
                    await db.commit()
                    used_backup = True
                else:
                    return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)
            except (json.JSONDecodeError, ValueError):
                return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)
        else:
            return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)

    token = create_access_token(subject=admin.username, role=admin.role)
    resp = JSONResponse({"ok": True, "message": "OK", "backup": used_backup})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
    resp.delete_cookie("vpn_preauth")
    return resp


@router.post("/2fa/disable")
async def twofa_disable(request: Request, db: AsyncSession = Depends(get_db)):
    from app.services.audit import AuditService

    admin_info = _require_permission(request, "system")
    body = await request.json()
    code = body.get("code", "")
    admin = await AdminService(db).get_by_username(admin_info["sub"])
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "message": "2FA не включена"}, status_code=400)
    totp = pyotp.TOTP(admin.totp_secret, interval=30, digits=6)
    if not totp.verify(code):
        return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)
    admin.totp_secret = None
    await db.commit()
    await AuditService(db).log(admin.id, "2fa_disabled", "admin", admin.id)
    await db.commit()
    return JSONResponse({"ok": True, "message": "2FA отключена"})


@router.get("/2fa/check")
async def twofa_check(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "system")
    admin = await AdminService(db).get_by_username(admin_info["sub"])
    return JSONResponse({"enabled": bool(admin and admin.totp_secret)})


@router.get("/2fa/export-backup-codes")
async def export_backup_codes(request: Request, db: AsyncSession = Depends(get_db)):
    """Generate fresh backup codes and export them as a downloadable text file."""
    from fastapi.responses import PlainTextResponse
    from app.services.audit import AuditService
    admin_info = _require_permission(request, "system")
    admin = await AdminService(db).get_by_username(admin_info["sub"])
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "message": "2FA не настроена"}, status_code=400)
    raw_codes = [_secrets.token_hex(4).upper() for _ in range(8)]
    hashed_codes = [hashlib.sha256(c.encode()).hexdigest() for c in raw_codes]
    admin.backup_codes = json.dumps(hashed_codes)
    await db.commit()
    await AuditService(db).log(admin.id, "2fa_backup_exported", "admin", admin.id)
    await db.commit()
    lines = [
        "Scorbium Dashboard — Резервные коды 2FA",
        f"Администратор: {admin.username}",
        f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Каждый код можно использовать только один раз.",
        "Храните их в надёжном месте.",
        "",
    ]
    for i, code in enumerate(raw_codes, 1):
        formatted = "-".join([code[j:j+4] for j in range(0, len(code), 4)])
        lines.append(f"{i}. {formatted}")
    lines.append("")
    lines.append("⚠️ Эти коды больше не отображаются в панели.")
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="backup_codes_{admin.username}.txt"'},
    )


@router.post("/2fa/regenerate-backup")
async def twofa_regenerate_backup(request: Request, db: AsyncSession = Depends(get_db)):
    from app.services.audit import AuditService

    admin_info = _require_permission(request, "system")
    admin = await AdminService(db).get_by_username(admin_info["sub"])
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "message": "2FA не включена"}, status_code=400)
    raw_codes = [secrets.token_hex(4).upper() for _ in range(8)]
    hashed_codes = [hashlib.sha256(c.encode()).hexdigest() for c in raw_codes]
    admin.backup_codes = json.dumps(hashed_codes)
    await db.commit()
    await AuditService(db).log(admin.id, "2fa_backup_regenerated", "admin", admin.id)
    await db.commit()
    return JSONResponse({"ok": True, "message": "Резервные коды обновлены", "backup_codes": raw_codes})
