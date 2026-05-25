"""VPN Nodes management routes."""

import html
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.services.pasarguard.pasarguard import PasarguardService

from .shared import _require_permission, _base_ctx, _toast, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def nodes_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "vpn.read")
    ctx = await _base_ctx(request, db, "nodes", admin_info)
    try:
        svc = PasarguardService()
        data = await svc.get_nodes()
        ctx["nodes"] = data.get("nodes", []) if isinstance(data, dict) else data
    except Exception:
        ctx["nodes"] = []
    return templates.TemplateResponse("nodes.html", ctx)


@router.get("/data", response_class=HTMLResponse)
async def nodes_data(request: Request):
    _require_permission(request, "vpn.read")
    from app.services.pasarguard.pasarguard import PasarguardService

    try:
        svc = PasarguardService()
        data = await svc.get_nodes()
        nodes = data.get("nodes", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(
            f"""<div class="p-3" style="color:var(--danger)">Ошибка: {html.escape(str(e))}</div>"""
        )

    if not nodes:
        return HTMLResponse("""<div class="p-3 text-muted">Нод нет</div>""")

    cards = ""
    for n in nodes:
        status = n.get("status", "")
        color = {
            "connected": "var(--success)",
            "connecting": "var(--warning)",
            "error": "var(--danger)",
        }.get(status, "var(--muted)")
        pulse = "animation: pulse-glow 2s infinite" if status == "connecting" else ""
        node_name = html.escape(str(n.get("name", "")))
        node_addr = html.escape(str(n.get("address", "")))
        node_id = html.escape(str(n.get("id", "")))
        cards += f"""
        <div class="col-md-6 col-xl-4">
          <div class="card glass p-3 h-100">
            <div class="d-flex align-items-center justify-content-between mb-2">
              <div class="d-flex align-items-center gap-2">
                <span style="width:10px;height:10px;border-radius:50%;background:{color};box-shadow:0 0 8px {color};{pulse}"></span>
                <span class="fw-semibold" style="color:var(--text)">{node_name}</span>
              </div>
              <span style="font-size:.7rem;color:var(--muted)">#{node_id}</span>
            </div>
            <div style="font-size:.78rem;color:var(--muted);margin-bottom:.5rem">{node_addr}</div>
            <div class="d-flex gap-3 mb-2" style="font-size:.75rem;color:var(--muted)">
              <span><i class="bi bi-hdd-network me-1"></i>{html.escape(str(n.get("port", "—")))}</span>
              <span><i class="bi bi-people me-1"></i>{html.escape(str(n.get("total_users", 0)))}</span>
            </div>
            <div class="d-flex gap-2 mt-auto">
              <button class="btn btn-sm btn-outline" hx-post="{config.web.panel_path(f'nodes/{node_id}/reconnect')}" hx-target="#nodes-grid" hx-swap="innerHTML">
                <i class="bi bi-arrow-clockwise me-1"></i>Переподключить
              </button>
            </div>
          </div>
        </div>"""

    return HTMLResponse(
        f"""<div class="row g-3" id="nodes-grid" hx-get="{config.web.panel_path('nodes/data')}" hx-trigger="every 30s" hx-swap="outerHTML">{cards}</div>"""
    )


@router.post("/{node_id}/reconnect", response_class=HTMLResponse)
async def reconnect_node(node_id: int, request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService

    try:
        svc = PasarguardService()
        await svc.reconnect_node(node_id)
        resp = Response(status_code=200)
        _toast(resp, f"Нода {node_id} переподключена")
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка: {str(e)[:100]}", "error")
    return resp


@router.post("/{node_id}/delete", response_class=HTMLResponse)
async def delete_node(
    node_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "system")
    try:
        svc = PasarguardService()
        await svc.remove_node(node_id)
        resp = HTMLResponse("")
        _toast(resp, f"Нода {node_id} удалена")
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка: {str(e)[:100]}", "error")
    return resp


@router.post("/add", response_class=HTMLResponse)
async def add_node(
    request: Request,
    name: str = Form(...),
    address: str = Form(...),
    port: int = Form(62050),
    api_port: int = Form(62051),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    try:
        svc = PasarguardService()
        await svc.add_node(name=name, address=address, port=port, api_port=api_port)
        resp = Response(status_code=200)
        _toast(resp, f"Нода {name} добавлена")
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка: {str(e)[:100]}", "error")
    return resp
