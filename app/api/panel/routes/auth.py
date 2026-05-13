"""Authentication and 2FA routes."""
import base64
import hashlib
import io
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import pyotp
import qrcode
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.admin import Admin, AdminRole
from app.services.admin import AdminService
from app.services.bot_settings import BotSettingsService
from app.utils.security import create_access_token, decode_access_token_full

from .shared import (
    SESSION_COOKIE, _require_auth, _require_permission,
    _base_ctx, templates, _set_cookie, _clear_cookie,
)

router = APIRouter()


PREAUTH_COOKIE = "vpn_preauth"
PREAUTH_MAX_AGE = 600


async def _login_template_context(
    request: Request,
    db: AsyncSession,
    *,
    error: str | None = None,
    show_2fa: bool = False,
) -> dict:
    settings = await BotSettingsService(db).get_all()
    return {
        "request": request,
        "error": error,
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "custom_logo": settings.get("custom_logo", ""),
        "show_2fa": show_2fa,
        "bot_language": settings.get("bot_language", "ru"),
    }


async def _verify_2fa_code(admin: Admin, code: str, db: AsyncSession) -> bool:
    normalized_code = code.upper().replace("-", "").strip()
    if not admin.totp_secret:
        return False

    totp = pyotp.TOTP(admin.totp_secret, interval=30, digits=6)
    if totp.verify(code):
        return True

    if not admin.backup_codes:
        return False

    try:
        hashed_codes = json.loads(admin.backup_codes)
        code_hash = hashlib.sha256(normalized_code.encode()).hexdigest()
        if code_hash not in hashed_codes:
            return False
        hashed_codes.remove(code_hash)
        admin.backup_codes = json.dumps(hashed_codes)
        await db.commit()
        return True
    except (json.JSONDecodeError, ValueError, TypeError):
        return False


def _make_preauth_token(admin: Admin) -> str:
    return create_access_token(
        subject=admin.username,
        role=admin.role,
        expires_delta=timedelta(minutes=10),
        extra={"type": "preauth"},
    )


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


@router.get("/login", response_class=HTMLResponse)
@router.get("/login/", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "login.html",
        await _login_template_context(request, db),
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    error_ctx = await _login_template_context(request, db, error="Неверный логин или пароль")

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
        return templates.TemplateResponse(request, "login.html", error_ctx)

    if not admin.is_active:
        return templates.TemplateResponse(
            request,
            "login.html",
            await _login_template_context(request, db, error="Аккаунт заблокирован"),
        )

    if admin.totp_secret:
        resp = templates.TemplateResponse(
            request,
            "login.html",
            await _login_template_context(request, db, show_2fa=True),
        )
        _set_cookie(
            resp,
            request,
            PREAUTH_COOKIE,
            _make_preauth_token(admin),
            max_age=PREAUTH_MAX_AGE,
        )
        _clear_cookie(resp, request, SESSION_COOKIE)
        return resp

    token = create_access_token(subject=admin.username, role=admin.role)
    resp = RedirectResponse(url="/panel/", status_code=302)
    _set_cookie(resp, request, SESSION_COOKIE, token, max_age=86400)
    _clear_cookie(resp, request, PREAUTH_COOKIE)
    return resp


@router.get("/logout")
async def logout(request: Request):
    resp = RedirectResponse(url="/panel/login", status_code=302)
    _clear_cookie(resp, request, SESSION_COOKIE)
    _clear_cookie(resp, request, PREAUTH_COOKIE)
    return resp


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
    preauth = request.cookies.get(PREAUTH_COOKIE, "")
    payload = decode_access_token_full(preauth) if preauth else None
    if not payload or payload.get("type") != "preauth":
        return templates.TemplateResponse(
            request,
            "login.html",
            await _login_template_context(
                request,
                db,
                error="Сначала введите логин и пароль",
            ),
        )

    admin = await AdminService(db).get_by_username(payload["sub"])
    if not admin or not admin.is_active or not admin.totp_secret:
        return templates.TemplateResponse(
            request,
            "login.html",
            await _login_template_context(
                request,
                db,
                error="Сессия 2FA истекла, войдите снова",
            ),
        )

    if not await _verify_2fa_code(admin, code, db):
        return templates.TemplateResponse(
            request,
            "login.html",
            await _login_template_context(
                request,
                db,
                error="Неверный код 2FA",
                show_2fa=True,
            ),
        )

    token = create_access_token(subject=admin.username, role=admin.role)
    resp = RedirectResponse(url="/panel/", status_code=302)
    _set_cookie(resp, request, SESSION_COOKIE, token, max_age=86400)
    _clear_cookie(resp, request, PREAUTH_COOKIE)
    return resp


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
    preauth = request.cookies.get(PREAUTH_COOKIE, "")
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

    used_backup = bool(admin.backup_codes) and not pyotp.TOTP(
        admin.totp_secret,
        interval=30,
        digits=6,
    ).verify(code)
    if not await _verify_2fa_code(admin, code, db):
        return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)

    token = create_access_token(subject=admin.username, role=admin.role)
    resp = JSONResponse({"ok": True, "message": "OK", "backup": used_backup})
    _set_cookie(resp, request, SESSION_COOKIE, token, max_age=86400)
    _clear_cookie(resp, request, PREAUTH_COOKIE)
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
    raw_codes = [secrets.token_hex(4).upper() for _ in range(8)]
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
