"""System monitoring & health check routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.slow_query import get_slow_queries
from app.api.dependencies import get_db

from .shared import _require_permission, _base_ctx, templates, _get_uptime

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def monitoring_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    from app.services.health import health_service

    labels = {
        "database": "PostgreSQL",
        "telegram_bot": "Telegram бот",
        "vpn_panel": "VPN панель",
        "payment_yookassa": "YooKassa",
        "payment_cryptobot": "CryptoBot",
        "payment_freekassa": "FreeKassa",
    }
    core_service_names = ["database", "telegram_bot", "vpn_panel"]
    payment_service_names = [
        "payment_yookassa",
        "payment_cryptobot",
        "payment_freekassa",
    ]

    entries = await health_service.check_all()
    services = {}
    for name, entry in entries.items():
        services[name] = {
            "name": name,
            "label": labels.get(name, name),
            "status": entry.status,
            "latency_ms": entry.latency_ms,
            "message": entry.message,
            "checked_at": entry.checked_at,
        }
    ctx = await _base_ctx(request, db, "monitoring")
    core_services = [services[name] for name in core_service_names if name in services]
    payment_services = [
        services[name]
        for name in payment_service_names
        if name in services and services[name]["status"] != "inactive"
    ]
    healthy_core = sum(1 for entry in core_services if entry["status"] == "healthy")
    problematic_core = sum(
        1 for entry in core_services if entry["status"] in {"degraded", "down"}
    )
    ctx["services"] = services
    ctx["core_services"] = core_services
    ctx["payment_services"] = payment_services
    ctx["monitoring_summary"] = {
        "healthy_core": healthy_core,
        "total_core": len(core_services),
        "problematic_core": problematic_core,
        "payment_count": len(payment_services),
    }
    ctx["slow_queries"] = get_slow_queries()[-50:]
    ctx["uptime"] = _get_uptime()
    return templates.TemplateResponse("monitoring.html", ctx)
