"""Backup & restore routes."""
import gzip
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path

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
from app.services.bot_settings import reset_bot_settings_cache

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()

_MAX_BACKUP = 100 * 1024 * 1024 
_REPO_ROOT = Path(__file__).resolve().parents[4]
_RESTORE_TIMEOUT = 180
_PUBLIC_SCHEMA_RESET_SQL = """DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO CURRENT_USER;
GRANT ALL ON SCHEMA public TO public;
"""


def _portable_dump_command(pg_uri: str) -> list[str]:
    return [
        "pg_dump",
        "--no-password",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        pg_uri,
    ]


def _format_subprocess_error(result: subprocess.CompletedProcess[bytes]) -> str:
    stderr = result.stderr.decode(errors="replace").strip()
    stdout = result.stdout.decode(errors="replace").strip()
    raw = stderr or stdout or "unknown subprocess error"
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return " | ".join(lines[-6:])[:700]


def _prepare_restore_sql(content: bytes) -> bytes:
    """Normalize imported SQL so legacy dumps restore predictably."""
    text = content.decode("utf-8-sig", errors="replace")
    filtered: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("\\CONNECT "):
            continue
        if upper.startswith("CREATE DATABASE ") or upper.startswith("ALTER DATABASE "):
            continue
        if upper.startswith("DROP SCHEMA ") and "PUBLIC" in upper:
            continue
        if upper.startswith("CREATE SCHEMA ") and "PUBLIC" in upper:
            continue
        if upper.startswith("ALTER SCHEMA ") and "PUBLIC" in upper:
            continue
        if upper.startswith("COMMENT ON SCHEMA ") and "PUBLIC" in upper:
            continue
        if upper.startswith("ALTER ") and " OWNER TO " in upper:
            continue
        if upper.startswith("GRANT ") or upper.startswith("REVOKE "):
            continue

        filtered.append(line)

    normalized = "\n".join(filtered).strip()
    return f"{_PUBLIC_SCHEMA_RESET_SQL}\n{normalized}\n".encode("utf-8")


def _run_post_restore_migrations() -> tuple[bool, str | None]:
    commands = [
        ["uv", "run", "python", "fix_alembic.py"],
        ["uv", "run", "alembic", "upgrade", "head"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=_RESTORE_TIMEOUT,
                cwd=_REPO_ROOT,
            )
        except FileNotFoundError:
            return False, f"Команда не найдена: {' '.join(cmd)}"
        except subprocess.TimeoutExpired:
            return False, f"Команда зависла: {' '.join(cmd)}"

        if result.returncode != 0:
            return False, _format_subprocess_error(result)

    return True, None


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
    cmd = _portable_dump_command(pg_uri)

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
    restore_sql = _prepare_restore_sql(content)
    cmd = ["psql", "--no-password", "-X", "-v", "ON_ERROR_STOP=1", "-1", "-f", "-", pg_uri]

    try:
        await db.rollback()
        await db.close()

        result = subprocess.run(
            cmd,
            input=restore_sql,
            capture_output=True,
            timeout=_RESTORE_TIMEOUT,
            cwd=_REPO_ROOT,
        )
        if result.returncode != 0:
            err = _format_subprocess_error(result)
            resp = Response(status_code=500)
            _toast(resp, f"Ошибка импорта: {err}", "error")
            return resp

        migrations_ok, migrations_error = _run_post_restore_migrations()
        if not migrations_ok:
            resp = Response(status_code=500)
            _toast(resp, f"Импорт выполнен, но миграции не применились: {migrations_error}", "error")
            return resp

        await reset_bot_settings_cache()
        resp = Response(status_code=200)
        _toast(resp, "База данных восстановлена и приведена к текущей схеме")
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
