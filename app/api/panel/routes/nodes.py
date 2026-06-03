"""VPN Nodes management routes."""

import html
from typing import Any

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.services.pasarguard.pasarguard import PasarguardService

from .shared import _require_permission, _base_ctx, _toast, templates

router = APIRouter()


def _node_status_meta(status: str) -> tuple[str, str, str, str]:
    status_norm = str(status or "").strip().lower()
    return {
        "connected": ("var(--success)", "", "Подключена", "connected"),
        "healthy": ("var(--success)", "", "Подключена", "connected"),
        "online": ("var(--success)", "", "Подключена", "connected"),
        "connecting": (
            "var(--warning)",
            "animation: pulse-glow 2s infinite",
            "Подключение",
            "connecting",
        ),
        "syncing": (
            "var(--warning)",
            "animation: pulse-glow 2s infinite",
            "Синхронизация",
            "connecting",
        ),
        "error": ("var(--danger)", "", "Ошибка", "error"),
        "failed": ("var(--danger)", "", "Ошибка", "error"),
        "offline": ("var(--danger)", "", "Офлайн", "offline"),
        "disconnected": ("var(--danger)", "", "Офлайн", "offline"),
        "disabled": ("var(--text-muted)", "", "Отключена", "disabled"),
    }.get(
        status_norm,
        ("var(--text-muted)", "", status_norm or "Неизвестно", "unknown"),
    )


def _node_summary_badges(nodes: list[dict[str, Any]]) -> str:
    counts = {
        "connected": 0,
        "connecting": 0,
        "error": 0,
        "offline": 0,
        "disabled": 0,
        "unknown": 0,
    }
    for node in nodes:
        _, _, _, bucket = _node_status_meta(str(node.get("status", "")))
        counts[bucket] = counts.get(bucket, 0) + 1

    badges = [
        ("Всего", len(nodes), "badge-open"),
        ("Подключены", counts["connected"], "badge-active"),
        ("Подключение", counts["connecting"], "badge-pending"),
        ("Ошибки", counts["error"] + counts["offline"], "badge-expired"),
    ]
    if counts["disabled"] or counts["unknown"]:
        badges.append(("Прочее", counts["disabled"] + counts["unknown"], "badge-closed"))

    return "".join(
        f'<span class="badge badge-custom {klass}" style="padding:.45rem .8rem">{label}: {value}</span>'
        for label, value, klass in badges
    )


def _render_nodes_grid(nodes: list[dict[str, Any]]) -> str:
    if not nodes:
        return """
        <div id="nodes-grid-shell">
          <div class="empty-state py-5">
            <i class="bi bi-server"></i>
            <div>Ноды пока не добавлены</div>
          </div>
        </div>
        """

    cards = ""
    for n in nodes:
        node_id = html.escape(str(n.get("id", "")))
        node_name = html.escape(str(n.get("name", "—")))
        node_addr = html.escape(str(n.get("address", "—")))
        node_port = html.escape(str(n.get("port", "—")))
        node_api_port = html.escape(str(n.get("api_port", "—")))
        node_conn = html.escape(str(n.get("connection_type", "—")))
        node_users = html.escape(str(n.get("total_users", 0)))
        node_message = html.escape(str(n.get("message", "")))
        color, pulse, status_label, _ = _node_status_meta(str(n.get("status", "")))

        cards += f"""
        <div class="col-md-6 col-xl-4">
          <div class="card h-100 p-3">
            <div class="d-flex align-items-start justify-content-between gap-3 mb-3">
              <div>
                <div class="d-flex align-items-center gap-2 mb-1">
                  <span style="width:10px;height:10px;border-radius:50%;background:{color};box-shadow:0 0 8px {color};{pulse}"></span>
                  <span class="fw-semibold" style="color:var(--text)">{node_name}</span>
                </div>
                <div style="font-size:.8rem;color:var(--text-muted)">{node_addr}</div>
              </div>
              <span class="badge badge-custom badge-open">#{node_id}</span>
            </div>

            <div class="d-grid gap-2 mb-3" style="font-size:.78rem">
              <div class="d-flex justify-content-between gap-3">
                <span style="color:var(--text-muted)">Статус</span>
                <span style="color:{color};font-weight:700">{status_label}</span>
              </div>
              <div class="d-flex justify-content-between gap-3">
                <span style="color:var(--text-muted)">Тип подключения</span>
                <span style="color:var(--text)">{node_conn}</span>
              </div>
              <div class="d-flex justify-content-between gap-3">
                <span style="color:var(--text-muted)">Node port</span>
                <span style="color:var(--text)">{node_port}</span>
              </div>
              <div class="d-flex justify-content-between gap-3">
                <span style="color:var(--text-muted)">API port</span>
                <span style="color:var(--text)">{node_api_port}</span>
              </div>
              <div class="d-flex justify-content-between gap-3">
                <span style="color:var(--text-muted)">Пользователи</span>
                <span style="color:var(--text)">{node_users}</span>
              </div>
            </div>

            {f'<div style="font-size:.75rem;color:var(--warning);margin-bottom:.85rem">{node_message}</div>' if node_message else ''}

            <div class="d-flex flex-wrap gap-2 mt-auto">
              <button
                class="btn btn-sm btn-outline"
                hx-post="{config.web.panel_path(f'nodes/{node_id}/reconnect')}"
                hx-target="#nodes-grid-shell"
                hx-swap="outerHTML">
                <i class="bi bi-arrow-clockwise me-1"></i>Переподключить
              </button>
              <button
                class="btn btn-sm btn-outline-danger"
                hx-post="{config.web.panel_path(f'nodes/{node_id}/delete')}"
                hx-confirm="Удалить ноду {node_name}?"
                hx-target="#nodes-grid-shell"
                hx-swap="outerHTML">
                <i class="bi bi-trash me-1"></i>Удалить
              </button>
            </div>
          </div>
        </div>
        """

    return f"""
    <div id="nodes-grid-shell">
      <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
        {_node_summary_badges(nodes)}
      </div>
      <div class="row g-3" hx-get="{config.web.panel_path('nodes/data')}" hx-trigger="every 30s" hx-swap="outerHTML">
        {cards}
      </div>
    </div>
    """


async def _load_nodes() -> list[dict[str, Any]]:
    svc = PasarguardService()
    data = await svc.get_nodes()
    if isinstance(data, dict):
        return list(data.get("nodes", []) or data.get("items", []) or [])
    return list(data or [])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def nodes_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "vpn.read")
    ctx = await _base_ctx(request, db, "nodes", admin_info)
    try:
        ctx["nodes"] = await _load_nodes()
    except Exception:
        ctx["nodes"] = []
    return templates.TemplateResponse("nodes.html", ctx)


@router.get("/data", response_class=HTMLResponse)
async def nodes_data(request: Request):
    _require_permission(request, "vpn.read")

    try:
        nodes = await _load_nodes()
    except Exception as e:
        return HTMLResponse(
            f"""<div class="p-3" style="color:var(--danger)">Ошибка: {html.escape(str(e))}</div>"""
        )

    return HTMLResponse(_render_nodes_grid(nodes))


@router.post("/{node_id}/reconnect", response_class=HTMLResponse)
async def reconnect_node(node_id: int, request: Request):
    _require_permission(request, "system")

    try:
        await PasarguardService().reconnect_node(node_id)
        nodes = await _load_nodes()
        resp = HTMLResponse(_render_nodes_grid(nodes))
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
        await PasarguardService().remove_node(node_id)
        nodes = await _load_nodes()
        resp = HTMLResponse(_render_nodes_grid(nodes))
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
    api_key: str = Form(...),
    server_ca: str = Form(...),
    connection_type: str = Form("grpc"),
    core_config_id: int = Form(1),
    keep_alive: int = Form(60),
    port: int = Form(62050),
    api_port: int = Form(62051),
    usage_coefficient: float = Form(1.0),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    try:
        await PasarguardService().add_node(
            name=name.strip(),
            address=address.strip(),
            api_key=api_key.strip(),
            server_ca=server_ca.strip(),
            connection_type=connection_type.strip() or "grpc",
            core_config_id=core_config_id,
            keep_alive=keep_alive,
            port=port,
            api_port=api_port,
            usage_coefficient=usage_coefficient,
        )
        nodes = await _load_nodes()
        resp = HTMLResponse(_render_nodes_grid(nodes))
        _toast(resp, f"Нода {name} добавлена")
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка: {str(e)[:100]}", "error")
    return resp
