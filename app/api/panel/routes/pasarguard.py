"""Pasarguard / Marzban panel routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.pasarguard.pasarguard import PasarguardService
from app.services.bot_settings import BotSettingsService

from .shared import _require_permission, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def pasarguard_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "pasarguard")
    settings = await BotSettingsService(db).get_all()
    ctx["bot_settings"] = settings
    try:
        svc = PasarguardService()
        ctx["marzban_stats"] = await svc.get_system_stats()
        ctx["marzban_ok"] = True
    except Exception:
        ctx["marzban_stats"] = None
        ctx["marzban_ok"] = False
    return templates.TemplateResponse("pasarguard.html", ctx)


@router.get("/users", response_class=HTMLResponse)
async def pg_users(request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService
    import html

    try:
        svc = PasarguardService()
        data = await svc.get_users(limit=50)
        users = data.get("users", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(f'<div style="color:#ef4444">Ошибка: {html.escape(str(e))}</div>')

    if not users:
        return HTMLResponse('<div class="text-center py-4" style="color:#8892a4">Пользователей нет</div>')

    from datetime import datetime as _dt

    def _fmt_date(d):
        if not d or d == "—":
            return "—"
        try:
            if isinstance(d, str):
                d = d.replace("Z", "+00:00")
                dt = _dt.fromisoformat(d)
            else:
                dt = _dt.fromtimestamp(d)
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return str(d)

    rows = ""
    for u in users:
        status = u.get("status", "")
        dot_class = {"active": "online", "expired": "offline", "disabled": "warning"}.get(status, "")
        status_label = {"active": "Активен", "expired": "Истёк", "disabled": "Отключён"}.get(status, status)
        download = u.get("download", 0) or 0
        upload = u.get("upload", 0) or 0
        used = round((download + upload) / 1073741824, 2)
        limit_bytes = u.get("data_limit", 0) or 0
        limit_gb = round(limit_bytes / 1073741824, 1) if limit_bytes else 0
        limit_str = f"{limit_gb} GB" if limit_bytes else "∞"
        username = html.escape(str(u.get("username", "")))
        expire = _fmt_date(u.get("expire"))
        created = _fmt_date(u.get("created_at"))
        traffic_color = "#22c55e" if limit_gb == 0 or used < limit_gb * 0.8 else ("#eab308" if used < limit_gb else "#ef4444")
        rows += f"""<div class="user-row" style="gap:.5rem;padding:.5rem .75rem">
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
              <span class="status-dot {dot_class}" style="flex-shrink:0"></span>
              <code style="color:var(--accent);font-size:.82rem">{username}</code>
              <span style="font-size:.7rem;color:{traffic_color};font-weight:600">{used} GB <span style="color:#8892a4;font-weight:400">/ {limit_str}</span></span>
            </div>
          </div>
          <div class="text-end" style="flex-shrink:0;min-width:130px">
            <div style="font-size:.72rem;color:var(--text-muted)">
              {status_label}
              <span style="color:#8892a4;margin-left:.4rem">до {expire}</span>
            </div>
            <div style="font-size:.65rem;color:#5a6478;margin-top:.1rem">с {created}</div>
          </div>
        </div>"""

    return HTMLResponse(f'<div class="p-1">{rows}</div>')


@router.get("/groups", response_class=HTMLResponse)
async def pg_groups(request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService
    import html

    try:
        svc = PasarguardService()
        groups = await svc.get_groups()
    except Exception as e:
        return HTMLResponse(f'<div style="color:#ef4444">Ошибка: {html.escape(str(e))}</div>')

    if not groups:
        return HTMLResponse('<div class="text-center py-4" style="color:#8892a4">Групп нет</div>')

    rows = ""
    for g in groups:
        disabled = g.get("is_disabled", False)
        inbounds = ", ".join(g.get("inbound_tags", []))
        group_name = html.escape(str(g.get("name", "")))
        rows += f"""<div class="group-row">
          <div style="flex:1;min-width:0">
            <code style="color:var(--accent);font-size:.85rem">{g.get("id")}</code>
            <span class="ms-2" style="font-size:.85rem;color:var(--text)">{group_name}</span>
            <div style="font-size:.7rem;color:#8892a4;margin-top:.15rem">{html.escape(inbounds)}</div>
          </div>
          <div class="text-end" style="flex-shrink:0">
            <span class="status-dot {"offline" if disabled else "online"}"></span>
            <span style="font-size:.75rem;color:var(--text-muted);margin-left:.3rem">{g.get("total_users", 0)} юз.</span>
          </div>
        </div>"""

    return HTMLResponse(f'<div class="p-2">{rows}</div>')


@router.get("/nodes", response_class=HTMLResponse)
async def pg_nodes(request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService
    import html

    try:
        svc = PasarguardService()
        data = await svc.get_nodes()
        nodes = data.get("nodes", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(f'<div style="color:#ef4444">Ошибка: {html.escape(str(e))}</div>')

    if not nodes:
        return HTMLResponse('<div class="text-center py-4" style="color:#8892a4">Нод нет</div>')

    rows = ""
    for n in nodes:
        status = n.get("status", "")
        dot_class = {"connected": "online", "connecting": "warning", "error": "offline"}.get(status, "")
        status_label = {"connected": "Подключена", "connecting": "Подключение", "error": "Ошибка"}.get(status, status)
        node_name = html.escape(str(n.get("name", "")))
        node_addr = html.escape(str(n.get("address", "")))
        rows += f"""<div class="node-row">
          <div style="flex:1;min-width:0">
            <code style="color:var(--accent);font-size:.85rem">{html.escape(str(n.get("id", "")))}</code>
            <span class="ms-2" style="font-size:.85rem;color:var(--text)">{node_name}</span>
            <div style="font-size:.7rem;color:#8892a4;margin-top:.15rem">{node_addr}</div>
          </div>
          <div class="text-end" style="flex-shrink:0">
            <span class="status-dot {dot_class}"></span>
            <span style="font-size:.75rem;color:var(--text-muted);margin-left:.3rem">{status_label}</span>
          </div>
        </div>"""

    return HTMLResponse(f'<div class="p-2">{rows}</div>')
