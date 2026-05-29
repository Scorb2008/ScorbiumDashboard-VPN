"""Shared utilities and template setup for panel routes."""

import html
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.models.payment import PaymentStatus
from app.schemas.user import UserDetail, UserRead
from app.services.branding_asset import BrandingAssetService
from app.services.payment import PaymentService
from app.services.support import SupportService
from app.utils.log import log
from app.utils.security import decode_access_token_full
from app.core.permissions import PERMISSIONS, has_permission

_tpl_path = Path(__file__).resolve().parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_tpl_path))
templates.env.globals["panel_base"] = config.web.panel_prefix
templates.env.globals["panel_root"] = config.web.panel_root
templates.env.globals["panel_url"] = lambda suffix="": config.web.panel_path(suffix)
templates.env.globals["absolute_panel_url"] = config.web.absolute_panel_url
templates.env.globals["legacy_panel_base"] = "/panel"

DEFAULT_PANEL_TIMEZONE = "Europe/Moscow"
PANEL_TIMEZONES = {
    "Europe/Moscow": "Москва",
    "Asia/Tehran": "Тегеран",
    "America/New_York": "Нью-Йорк",
}

SESSION_COOKIE = "vpn_session"

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
    {"id": "cabinet", "label": "📱 Кабинет", "web_app": ""},
    {"id": "admin_panel", "label": "⚙️ Админ панель", "url": ""},
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
    [{"id": "cabinet", "label": "📱 Кабинет", "web_app": ""}],
    [{"id": "admin_panel", "label": "⚙️ Админ панель", "url": ""}],
]

_startup_time = datetime.now(timezone.utc)
_STATE_UNSET = object()


@lru_cache(maxsize=len(PANEL_TIMEZONES))
def _zoneinfo(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolve_panel_timezone(raw_tz: str | None) -> str:
    tz_name = (raw_tz or "").strip()
    return tz_name if tz_name in PANEL_TIMEZONES else DEFAULT_PANEL_TIMEZONE


def _get_request_timezone_name(request: Request | None) -> str:
    if request is None:
        return DEFAULT_PANEL_TIMEZONE
    return _resolve_panel_timezone(request.cookies.get("panel_timezone"))


def _format_datetime_for_timezone(
    value: datetime | None,
    tz_name: str,
    fmt: str = "%d.%m.%Y %H:%M",
    fallback: str = "—",
) -> str:
    normalized = _normalize_datetime(value)
    if normalized is None:
        return fallback
    return normalized.astimezone(_zoneinfo(tz_name)).strftime(fmt)


@pass_context
def _jinja_datetime_filter(
    context,
    value: datetime | None,
    fmt: str = "%d.%m.%Y %H:%M",
    fallback: str = "—",
) -> str:
    request = context.get("request")
    tz_name = _get_request_timezone_name(request)
    return _format_datetime_for_timezone(value, tz_name, fmt=fmt, fallback=fallback)


templates.env.filters["dt"] = _jinja_datetime_filter


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


def _is_secure_request(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _set_cookie(
    response: Response,
    request: Request,
    key: str,
    value: str,
    *,
    max_age: int,
    httponly: bool = True,
) -> None:
    response.set_cookie(
        key,
        value,
        httponly=httponly,
        samesite="lax",
        secure=_is_secure_request(request),
        max_age=max_age,
        path="/",
    )


def _clear_cookie(response: Response, request: Request, key: str) -> None:
    response.delete_cookie(
        key,
        path="/",
        secure=_is_secure_request(request),
        httponly=True,
        samesite="lax",
    )


def _get_admin_info(request: Request) -> dict | None:
    """Extract admin info (sub + role) from session cookie. Returns None if invalid."""
    if getattr(request.state, "revoked_panel_session", False):
        return None

    state_info: Any = getattr(request.state, "panel_admin_info", _STATE_UNSET)
    if state_info is not _STATE_UNSET:
        return state_info

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    info = decode_access_token_full(token)
    if not info:
        return None
    role = str(info.get("role", "")).strip().lower()
    if role not in PERMISSIONS:
        return None
    return info


def _check_session(request: Request) -> bool:
    return _get_admin_info(request) is not None


def _require_auth(request: Request) -> dict:
    """Enforce authentication. Returns {"sub": str, "role": str}."""
    info = _get_admin_info(request)
    if info is None:
        try:
            cookie_present = bool(request.cookies.get(SESSION_COOKIE))
        except Exception:
            cookie_present = False

        is_htmx = request.headers.get("HX-Request") == "true"
        is_api = "/api/" in str(request.url.path)
        is_json = "application/json" in request.headers.get("accept", "")

        log.warning(
            "Panel auth failed: path={} hx={} api={} json={} cookie_present={}",
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
                headers={"HX-Redirect": config.web.panel_login_path},
            )
        raise _redirect(config.web.panel_login_path)
    return info


def _require_permission(request: Request, permission: str) -> dict:
    """Enforce authentication and check permission. Returns admin info dict."""
    info = _require_auth(request)
    role = info.get("role") if isinstance(info, dict) else None

    if not has_permission(role, permission):
        log.warning(
            "Panel permission denied: path={} role={} required={} sub={}",
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
                '<div class="mt-1 text-end">'
                '<button class="btn btn-sm py-0 px-2" style="font-size:.65rem;color:#00d4aa;background:none;border:1px solid rgba(0,212,170,.3)" '
                "onclick=\"document.querySelector('[name=text]').value=''\">✏️ Ответить</button>"
                "</div>"
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
    custom_logo = await BrandingAssetService(db).get_logo_url()
    now = datetime.now(timezone.utc)
    selected_timezone = _get_request_timezone_name(request)
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
        "selected_timezone": selected_timezone,
        "current_time": _format_datetime_for_timezone(
            now, selected_timezone, fmt="%H:%M:%S"
        ),
        "current_date": _format_datetime_for_timezone(
            now, selected_timezone, fmt="%d.%m.%Y"
        ),
        "time_moscow": _format_datetime_for_timezone(
            now, "Europe/Moscow", fmt="%H:%M:%S"
        ),
        "time_tehran": _format_datetime_for_timezone(
            now, "Asia/Tehran", fmt="%H:%M:%S"
        ),
        "time_us": _format_datetime_for_timezone(
            now, "America/New_York", fmt="%H:%M:%S"
        ),
        "csrf_token": request.cookies.get("csrf_token", ""),
        "custom_logo": custom_logo,
        "open_alerts": 0,
    }
