"""Backup & restore routes."""
import gzip
import io
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, Response as StreamingResponse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.payment import Payment
from app.models.support import SupportTicket
from app.models.user import User
from app.models.vpn_key import VpnKey

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()

_MAX_BACKUP = 100 * 1024 * 1024 


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def backup_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "backup")

    ctx["db_stats"] = {
        "users": (await db.execute(select(func.count()).select_from(User))).scalar_one(),
        "vpn_keys": (await db.execute(select(func.count()).select_from(VpnKey))).scalar_one(),
        "payments": (await db.execute(select(func.count()).select_from(Payment))).scalar_one(),
        "tickets": (await db.execute(select(func.count()).select_from(SupportTicket))).scalar_one(),
    }

    return templates.TemplateResponse("backup.html", ctx)


@router.get("/export")
async def backup_export(request: Request, format: str = "sql"):
    _require_permission(request, "system")
    pg_uri = config.database.sync_dsn
    cmd = ["pg_dump", "--no-password", "--clean", "--if-exists", pg_uri]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")[:300]
            return Response(content=f"pg_dump error: {err}", status_code=500)
        sql_bytes = result.stdout
    except FileNotFoundError:
        return Response(content="pg_dump not found. Install postgresql-client.", status_code=500)
    except subprocess.TimeoutExpired:
        return Response(content="pg_dump timed out", status_code=500)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if format == "gz":
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(sql_bytes)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="backup_{ts}.sql.gz"'},
        )

    return StreamingResponse(
        io.BytesIO(sql_bytes),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="backup_{ts}.sql"'},
    )


@router.post("/import", response_class=HTMLResponse)
async def backup_import(
    request: Request,
    file: UploadFile = File(...),
    confirm: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    if confirm != "yes":
        resp = Response(status_code=400)
        _toast(resp, "Подтвердите восстановление", "error")
        return resp

    content = await file.read()
    if len(content) > _MAX_BACKUP:
        resp = Response(status_code=413)
        _toast(resp, "Файл слишком большой (макс. 100MB)", "error")
        return resp

    filename = file.filename or ""
    if filename.endswith(".gz"):
        try:
            content = gzip.decompress(content)
        except Exception:
            resp = Response(status_code=400)
            _toast(resp, "Не удалось распаковать .gz файл", "error")
            return resp

    pg_uri = config.database.sync_dsn
    cmd = ["psql", "--no-password", "-f", "-", pg_uri]

    try:
        result = subprocess.run(cmd, input=content, capture_output=True, timeout=120)
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")[:300]
            resp = Response(status_code=500)
            _toast(resp, f"Ошибка импорта: {err}", "error")
            return resp
        resp = Response(status_code=200)
        _toast(resp, "База данных восстановлена")
    except FileNotFoundError:
        resp = Response(status_code=500)
        _toast(resp, "psql not found", "error")
    except subprocess.TimeoutExpired:
        resp = Response(status_code=500)
        _toast(resp, "Импорт завис", "error")

    return resp


@router.post("/database/clear")
async def clear_database(request: Request, db: AsyncSession = Depends(get_db)):
    """Clear all user data while preserving settings and admins."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    from sqlalchemy import text

    try:
        await db.execute(text("DELETE FROM ticket_messages"))
        await db.execute(text("DELETE FROM referrals"))
        await db.execute(text("DELETE FROM support_tickets"))
        await db.execute(text("DELETE FROM payments"))
        await db.execute(text("DELETE FROM vpn_keys"))
        await db.execute(text("DELETE FROM users"))
        await db.commit()
        return JSONResponse({"ok": True, "message": "База данных успешно очищена"})
    except Exception as e:
        await db.rollback()
        return JSONResponse({"ok": False, "message": f"Ошибка: {str(e)}"}, status_code=500)
