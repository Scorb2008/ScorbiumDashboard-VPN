"""Bot keyboard layout editor routes."""
import json as _json
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.bot_settings import BotSettingsService
from app.services.telegram_notify import TelegramNotifyService

from .shared import (
    _require_permission, _toast, _base_ctx, templates,
    _ALL_BUTTONS, _DEFAULT_LAYOUT,
)

router = APIRouter()

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
    [{"id": "top_referrers", "label": "🏆 Топ реферевов", "callback": "top_referrers"}],
    [{"id": "support", "label": "💬 Поддержка", "callback": "support"}],
    [{"id": "cabinet", "label": "📱 Кабинет", "web_app": ""}],
    [{"id": "admin_panel", "label": "⚙️ Админ панель", "url": ""}],
]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def keyboard_editor(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "keyboard")
    ctx["bot_settings"] = await BotSettingsService(db).get_all()
    ctx["bot_info"] = await TelegramNotifyService().get_bot_info()
    ctx["all_buttons"] = _ALL_BUTTONS
    ctx["default_layout"] = _DEFAULT_LAYOUT
    raw = await BotSettingsService(db).get("keyboard_layout")
    try:
        ctx["layout"] = _json.loads(raw) if raw else _DEFAULT_LAYOUT
    except Exception:
        ctx["layout"] = _DEFAULT_LAYOUT
    ctx["welcome_text"] = (
        await BotSettingsService(db).get("welcome_message")
        or "👋 Привет! Выбери действие:"
    )
    used_ids = [b["id"] for row in ctx["layout"] for b in row]
    ctx["used_ids"] = used_ids
    return templates.TemplateResponse("keyboard_editor.html", ctx)


@router.post("/save")
async def keyboard_save(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    body = await request.json()
    layout = body.get("layout", _DEFAULT_LAYOUT)
    await BotSettingsService(db).set("keyboard_layout", _json.dumps(layout))
    await db.commit()
    return {"ok": True}


@router.post("/styles")
async def keyboard_styles(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    body = await request.json()
    styles = body.get("styles", {})
    svc = BotSettingsService(db)
    for btn_id, style in styles.items():
        await svc.set(f"btn_style_{btn_id}", style)
    await db.commit()
    return {"ok": True}
