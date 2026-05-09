"""Shared utilities and template setup for panel routes."""
import gzip
import html
import io
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import cast, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.payment import PaymentStatus, PaymentType
from app.models.support import TicketPriority, TicketStatus
from app.schemas.user import UserDetail, UserRead
from app.services.bot_settings import BotSettingsService
from app.services.broadcast import BroadcastService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.promo import PromoService
from app.services.referral import ReferralService
from app.services.support import SupportService
from app.services.telegram_notify import TelegramNotifyService
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService
from app.utils.log import log
from app.utils.security import create_access_token, decode_access_token_full
from app.core.permissions import has_permission
from app.services.admin import AdminService
from app.models.admin import AdminRole
from app.services.export import ExportService
from app.services.notification import notification_manager

_tpl_path = Path(__file__).resolve().parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_tpl_path))

SESSION_COOKIE = "vpn_session"

# Mini App tokens stored in memory: {token: expiry_timestamp}
import time as _time
_mini_app_tokens: dict[str, float] = {}

_ALL_BUTTONS = [
    {"id": "my_keys", "label": "🔑 Мои подписки", "callback": "my_keys"},
    {"id": "buy", "label": "💳 Купить", "callback": "buy"},
    {"id": "profile", "label": "👤 Профиль", "callback": "profile"},
    {"id": "balance", "label": "💰 Баланс", "callback": "balance"},
    {"id": "promo", "label": "🎁 Промокод", "callback": "enter_promo"},
    {"id": "support", "label": "💬 Поддержка", "callback": "support"},
    {"id": "connect", "label": "📲 Как подключить", "callback": "connect:menu"},
    {"id": "about", "label": "ℹ️ О проекте", "callback": "about"},
    {"id": "servers", "label": "🌐 Серверы", "callback": "servers"},
    {"id": "top_referrers", "label": "🏆 Топ рефералов", "callback": "top_referrers"},
    {"id": "status", "label": "📊 Статус", "callback": "status_cmd"},
    {"id": "language", "label": "🌐 Язык", "callback": "language"},
    {"id": "trial", "label": "🎁 Пробный период", "callback": "trial"},
    {"id": "miniapp", "label": "📱 Открыть", "callback": "miniapp"},
]

_DEFAULT_LAYOUT = [
    [{"id": "my_keys", "label": "🔑 Мои подписки", "callback": "my_keys"}],
    [{"id": "buy", "label": "💳 Купить", "callback": "buy"}],
    [
        {"id": "balance", "label": "💰 Баланс", "callback": "balance"},
        {"id": "promo", "label": "🎁 Промокод", "callback": "enter_promo"},
    ],
    [
        {"id": "connect", "label": "📲 Как подключить", "callback": "connect:menu"},
        {"id": "about", "label": "ℹ️ О проекте", "callback": "about"},
    ],
    [
        {"id": "profile", "label": "👤 Профиль", "callback": "profile"},
        {"id": "servers", "label": "🌐 Серверы", "callback": "servers"},
    ],
    [{"id": "top_referrers", "label": "🏆 Топ рефералов", "callback": "top_referrers"}],
    [{"id": "support", "label": "💬 Поддержка", "callback": "support"}],
]

_startup_time = datetime.now(timezone.utc)


def _get_uptime() -> str:
    delta = datetime.now(timezone.utc) - _startup_time
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


_NOTIFY_SERVICES = [
    {"key": "database", "label": "PostgreSQL", "icon": "🗄️"},
    {"key": "telegram_bot", "label": "Telegram Bot", "icon": "🤖"},
    {"key": "vpn_panel", "label": "VPN панель", "icon": "🌐"},
    {"key": "yookassa", "label": "YooKassa", "icon": "💳"},
    {"key": "cryptobot", "label": "CryptoBot", "icon": "₿"},
    {"key": "freekassa", "label": "FreeKassa", "icon": "⚡"},
]


def _toast(resp: Response, message: str, kind: str = "success") -> None:
    """Unicode-safe toast via HX-Trigger JSON header."""
    resp.headers["HX-Trigger"] = json.dumps(
        {"showToast": {"msg": message, "type": kind}}
    )


def _get_admin_info(request: Request) -> dict | None:
    """Extract admin info (sub + role) from session cookie. Returns None if invalid."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return decode_access_token_full(token)


def _check_session(request: Request) -> bool:
    return _get_admin_info(request) is not None


def _require_auth(request: Request) -> dict:
    """Enforce authentication. Returns {"sub": str, "role": str}."""
    info = _get_admin_info(request)
    if info is None:
        # Helpful debug info for 401/redirect/403 cascades
        try:
            cookie_present = bool(request.cookies.get(SESSION_COOKIE))
        except Exception:
            cookie_present = False

        is_htmx = request.headers.get("HX-Request") == "true"
        is_api = "/api/" in str(request.url.path)
        is_json = "application/json" in request.headers.get("accept", "")

        log.warning(
            "Panel auth failed: path=%s hx=%s api=%s json=%s cookie_present=%s",
            request.url.path,
            is_htmx,
            is_api,
            is_json,
            cookie_present,
        )

        if is_api or is_json:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Not authenticated")
        if is_htmx:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=200,
                headers={"HX-Redirect": "/panel/login"},
            )
        raise _redirect("/panel/login")
    return info


def _require_permission(request: Request, permission: str) -> dict:
    """Enforce authentication and check permission. Returns admin info dict."""
    info = _require_auth(request)
    role = info.get("role") if isinstance(info, dict) else None

    if not has_permission(role, permission):
        log.warning(
            "Panel permission denied: path=%s role=%s required=%s sub=%s",
            request.url.path,
            role,
            permission,
            info.get("sub") if isinstance(info, dict) else None,
        )

        is_htmx = request.headers.get("HX-Request") == "true"
        from fastapi import HTTPException
        if is_htmx:
            raise HTTPException(
                status_code=200,
                headers={
                    "HX-Trigger": json.dumps(
                        {"showToast": {"msg": "Недостаточно прав", "type": "error"}}
                    )
                },
            )
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return info


def _redirect(url: str):
    from fastapi import HTTPException
    raise HTTPException(status_code=302, headers={"Location": url})


def _to_detail(u) -> UserDetail:
    return UserDetail(
        **UserRead.model_validate(u).model_dump(),
        subscriptions_count=len(u.vpn_keys),
        payments_count=len(u.payments),
        vpn_keys_count=len(u.vpn_keys),
    )


def _render_messages(ticket) -> str:
    """Render ticket messages as HTML for HTMX swap."""
    if not ticket:
        return ""
    msgs_html = ""
    for msg in ticket.messages:
        align = "justify-content-end" if msg.is_admin else ""
        bg = "rgba(0,212,170,.2)" if msg.is_admin else "rgba(255,255,255,.05)"
        sender = (
            '<i class="bi bi-shield-check me-1" style="color:#00d4aa"></i>Поддержка'
            if msg.is_admin
            else f'<i class="bi bi-person me-1"></i>Пользователь {html.escape(str(msg.sender_id))}'
        )
        reply_btn = ""
        if not msg.is_admin:
            reply_btn = (
                f'<div class="mt-1 text-end">'
                f'<button class="btn btn-sm py-0 px-2" style="font-size:.65rem;color:#00d4aa;background:none;border:1px solid rgba(0,212,170,.3)" '
                f"onclick=\"document.querySelector('[name=text]').value=''\">✏️ Ответить</button>"
                f"</div>"
            )
        safe_text = html.escape(str(msg.text)) if msg.text else ""
        msgs_html += (
            f'<div class="mb-3 d-flex {align}">'
            f'<div style="max-width:80%;background:{bg};border-radius:10px;padding:.6rem .9rem;font-size:.85rem;color:#c8d0e0">'
            f'<div style="font-size:.7rem;color:#8892a4;margin-bottom:.3rem">{sender}</div>'
            f"{safe_text}{reply_btn}</div></div>"
        )
    return msgs_html


async def _base_ctx(
    request: Request, db: AsyncSession, active: str, admin_info: dict | None = None
) -> dict:
    if admin_info is None:
        admin_info = _get_admin_info(request)
    open_tickets = await SupportService(db).count_open()
    pending_payments = await PaymentService(db).count_by_status(PaymentStatus.PENDING)
    role = admin_info["role"] if admin_info else ""
    settings = await BotSettingsService(db).get_all()
    custom_logo = settings.get("custom_logo", "")
    now = datetime.now(timezone.utc)
    moscow_tz = timezone(timedelta(hours=3))
    iran_tz = timezone(timedelta(hours=3, minutes=30))
    us_east = timezone(timedelta(hours=-5))
    return {
        "request": request,
        "active": active,
        "open_tickets": open_tickets,
        "pending_payments": pending_payments,
        "bot_username": None,
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "vpn_panel_type": "marzban",
        "admin_role": role,
        "admin_username": admin_info["sub"] if admin_info else "",
        "has_perm": has_permission,
        "current_time": now.strftime("%H:%M"),
        "current_date": now.strftime("%d %B %Y"),
        "time_moscow": now.astimezone(moscow_tz).strftime("%H:%M"),
        "time_tehran": now.astimezone(iran_tz).strftime("%H:%M"),
        "time_us": now.astimezone(us_east).strftime("%H:%M"),
        "csrf_token": request.cookies.get("csrf_token", ""),
        "custom_logo": custom_logo,
        "open_alerts": 0,
    }
