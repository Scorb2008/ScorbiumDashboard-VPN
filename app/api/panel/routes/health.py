"""Health JSON endpoint for panel monitoring widget."""
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db

from .shared import _require_permission

router = APIRouter()


@router.get("/health/json")
async def panel_health_json(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    from app.services.health import health_service
    entries = await health_service.check_all()
    result = {}
    for name, entry in entries.items():
        result[name] = {
            "status": entry.status,
            "latency_ms": entry.latency_ms,
            "message": entry.message,
            "checked_at": (
                entry.checked_at.isoformat()
                if isinstance(entry.checked_at, datetime)
                else entry.checked_at
            ),
        }
    return JSONResponse(result)
