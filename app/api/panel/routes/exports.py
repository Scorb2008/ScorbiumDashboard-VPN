"""Data export routes (CSV/XLSX)."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.export import ExportService
import io
from .shared import _require_permission

router = APIRouter()


@router.get("/users")
async def export_users(
    request: Request,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "export")
    data = await ExportService(db).export_users(fmt=format)
    ext = "xlsx" if format == "xlsx" else "csv"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if ext == "xlsx"
        else "text/csv"
    )
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="users_{ts}.{ext}"'},
    )


@router.get("/payments")
async def export_payments(
    request: Request,
    format: str = "csv",
    status: Optional[str] = None,
    payment_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "export")
    data = await ExportService(db).export_payments(
        fmt=format,
        status=status,
        payment_type=payment_type,
        date_from=date_from,
        date_to=date_to,
    )
    ext = "xlsx" if format == "xlsx" else "csv"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if ext == "xlsx"
        else "text/csv"
    )
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="payments_{ts}.{ext}"'},
    )


@router.get("/subscriptions")
async def export_subscriptions(
    request: Request,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "export")
    data = await ExportService(db).export_subscriptions(fmt=format)
    ext = "xlsx" if format == "xlsx" else "csv"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if ext == "xlsx"
        else "text/csv"
    )
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="subscriptions_{ts}.{ext}"'
        },
    )
