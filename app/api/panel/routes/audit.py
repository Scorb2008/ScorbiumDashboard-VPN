"""Audit log routes."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db

from .shared import _require_permission, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def audit_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "audit")
    from app.services.audit import AuditService
    ctx["entries"] = await AuditService(db).get_recent(limit=None)
    return templates.TemplateResponse("audit.html", ctx)
