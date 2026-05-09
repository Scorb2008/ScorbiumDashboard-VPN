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
        return HTMLResponse('<div style="color:#8892a4">Пользователей нет</div>')

    rows = ""
    for u in users:
        status = u.get("status", "")
        color = {"active": "#22c55e", "expired": "#ef4444", "disabled": "#eab308"}.get(status, "#8892a4")
        used = round((u.get("used_traffic", 0) or 0) / 1073741824, 2)
        limit = u.get("data_limit", 0) or 0
        limit_str = f"{round(limit / 1073741824, 1)} GB" if limit else "∞"
        username = html.escape(str(u.get("username", "")))
        expire = html.escape(str(u.get("expire", "—") or "—"))
        rows += f"""<tr class="user-row">
          <td><code style="color:var(--accent)">{username}</code></td>
          <td><span style="color:{color};font-size:.75rem">{html.escape(str(status))}</span></td>
          <td style="font-size:.78rem;color:#8892a4">{used} / {limit_str}</td>
          <td style="font-size:.75rem;color:#8892a4">{expire}</td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
      <table class="table mb-0">
        <thead><tr><th>Username</th><th>Статус</th><th>Трафик</th><th>Истекает</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>""")


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
        return HTMLResponse('<div style="color:#8892a4">Групп нет</div>')

    rows = ""
    for g in groups:
        disabled = "🔴" if g.get("is_disabled") else "✅"
        inbounds = ", ".join(g.get("inbound_tags", []))
        group_name = html.escape(str(g.get("name", "")))
        rows += f"""<tr>
          <td><code style="color:var(--accent)">{g.get("id")}</code></td>
          <td class="text-white">{group_name}</td>
          <td style="font-size:.75rem;color:#8892a4">{html.escape(inbounds)}</td>
          <td>{disabled}</td>
          <td style="color:#8892a4">{g.get("total_users", 0)}</td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
      <table class="table mb-0">
        <thead><tr><th>ID</th><th>Название</th><th>Inbounds</th><th>Статус</th><th>Юзеров</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>""")


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
        return HTMLResponse('<div style="color:#8892a4">Нод нет</div>')

    rows = ""
    for n in nodes:
        status = n.get("status", "")
        color = {"connected": "#22c55e", "connecting": "#eab308", "error": "#ef4444"}.get(status, "#8892a4")
        node_name = html.escape(str(n.get("name", "")))
        node_addr = html.escape(str(n.get("address", "")))
        rows += f"""<tr>
          <td><code style="color:var(--accent)">{html.escape(str(n.get("id", "")))}</code></td>
          <td class="text-white">{node_name}</td>
          <td style="font-size:.8rem;color:#8892a4">{node_addr}</td>
          <td><span style="color:{color};font-size:.75rem">{html.escape(str(status))}</span></td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
      <table class="table mb-0">
        <thead><tr><th>ID</th><th>Название</th><th>Адрес</th><th>Статус</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>""")
