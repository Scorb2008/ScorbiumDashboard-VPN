"""System monitoring & health check routes."""
import html
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.health import health_service, ServiceStatus
from app.services.slow_query import get_slow_queries
from app.api.dependencies import get_db

from .shared import _require_permission, _toast, _base_ctx, templates, _get_uptime, _startup_time

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def monitoring_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    from app.services.health import health_service
    entries = await health_service.check_all()
    services = {}
    for name, entry in entries.items():
        services[name] = {
            "status": entry.status,
            "latency_ms": entry.latency_ms,
            "message": entry.message,
            "checked_at": entry.checked_at,
        }
    ctx = await _base_ctx(request, db, "monitoring")
    ctx["services"] = services
    ctx["slow_queries"] = get_slow_queries()[-50:]
    ctx["uptime"] = _get_uptime()
    return templates.TemplateResponse("monitoring.html", ctx)


@router.get("/health/json")
async def health_json(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    from app.services.health import health_service
    entries = await health_service.check_all()
    result = {}
    for name, entry in entries.items():
        result[name] = {
            "status": entry.status,
            "latency_ms": entry.latency_ms,
            "message": entry.message,
            "checked_at": entry.checked_at.isoformat() if entry.checked_at else None,
        }
    return JSONResponse(result)
