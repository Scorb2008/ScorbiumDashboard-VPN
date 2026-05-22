import io
import json
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import UploadFile
from sqlalchemy import select
from starlette.requests import Request

import app.api.panel.routes.backup as backup_routes
from app.api.panel.routes.backup import (
    _PUBLIC_SCHEMA_RESET_SQL,
    _format_subprocess_error,
    _run_post_restore_migrations,
    backup_export,
    backup_import,
    _prepare_restore_sql,
)
from app.models.bot_settings import BotSettings
from app.services.bot_settings import sync_deployment_url_settings


def test_prepare_restore_sql_strips_transaction_timeout_set():
    restored = _prepare_restore_sql(
        b"SET statement_timeout = 0;\nSET transaction_timeout = 0;\nCREATE TABLE demo(id int);\n"
    ).decode("utf-8")

    assert restored.startswith(f"{_PUBLIC_SCHEMA_RESET_SQL}\n")
    assert "SET statement_timeout = 0;" in restored
    assert "SET transaction_timeout = 0;" not in restored
    assert "CREATE TABLE demo(id int);" in restored


def test_prepare_restore_sql_strips_transaction_timeout_set_config():
    restored = _prepare_restore_sql(
        b"SELECT pg_catalog.set_config('transaction_timeout', '0', false);\nSELECT 1;\n"
    ).decode("utf-8")

    assert "transaction_timeout" not in restored
    assert "SELECT 1;" in restored


def test_prepare_restore_sql_strips_restrict_commands():
    restored = _prepare_restore_sql(
        b"\\restrict token123\nCREATE TABLE demo(id int);\n\\unrestrict token123\n"
    ).decode("utf-8")

    assert "\\restrict" not in restored
    assert "\\unrestrict" not in restored
    assert "CREATE TABLE demo(id int);" in restored


def test_format_subprocess_error_prefers_actual_error_over_drop_notice():
    result = CompletedProcess(
        args=["psql"],
        returncode=1,
        stdout=b"",
        stderr=(
            b"NOTICE: drop cascades to table broadcasts\n"
            b"NOTICE: drop cascades to table admins\n"
            b'psql:<stdin>:18: ERROR: unrecognized configuration parameter "transaction_timeout"\n'
        ),
    )

    formatted = _format_subprocess_error(result)

    assert (
        'ERROR: unrecognized configuration parameter "transaction_timeout"' in formatted
    )
    assert "NOTICE: drop cascades" not in formatted


def _make_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/panel/backup/import",
            "raw_path": b"/panel/backup/import",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


async def test_backup_import_success_without_request_db_session(monkeypatch):
    reset_cache = AsyncMock()
    sync_urls = AsyncMock(return_value={})
    run_subprocess = AsyncMock(
        return_value=CompletedProcess(
            args=["psql"], returncode=0, stdout=b"", stderr=b""
        )
    )
    run_migrations = AsyncMock(return_value=(True, None))

    monkeypatch.setattr(
        backup_routes, "_require_permission", lambda request, permission: None
    )
    monkeypatch.setattr(
        backup_routes,
        "config",
        SimpleNamespace(
            database=SimpleNamespace(sync_dsn="postgresql://restore-target")
        ),
    )
    monkeypatch.setattr(backup_routes, "_run_subprocess", run_subprocess)
    monkeypatch.setattr(backup_routes, "_run_post_restore_migrations", run_migrations)
    monkeypatch.setattr(
        backup_routes, "_sync_deployment_settings_after_restore", sync_urls
    )
    monkeypatch.setattr(backup_routes, "reset_bot_settings_cache", reset_cache)

    response = await backup_import(
        _make_request(),
        file=UploadFile(
            filename="backup.sql", file=io.BytesIO(b"CREATE TABLE demo(id int);\n")
        ),
        confirm="yes",
    )

    payload = json.loads(response.headers["HX-Trigger"])

    assert response.status_code == 200
    sync_urls.assert_awaited_once()
    run_subprocess.assert_awaited_once()
    run_migrations.assert_awaited_once()
    assert payload["showToast"]["type"] == "success"
    reset_cache.assert_awaited_once()


async def test_backup_import_rejects_empty_file(monkeypatch):
    monkeypatch.setattr(
        backup_routes, "_require_permission", lambda request, permission: None
    )

    response = await backup_import(
        _make_request(),
        file=UploadFile(filename="backup.sql", file=io.BytesIO(b"   \n")),
        confirm="yes",
    )

    payload = json.loads(response.headers["HX-Trigger"])

    assert response.status_code == 400
    assert payload["showToast"]["type"] == "error"
    assert "пустой" in payload["showToast"]["msg"].lower()


async def test_backup_export_uses_async_subprocess_runner(monkeypatch):
    sql_bytes = b"CREATE TABLE demo(id int);\n"
    run_subprocess = AsyncMock(
        return_value=CompletedProcess(
            args=["pg_dump"], returncode=0, stdout=sql_bytes, stderr=b""
        )
    )

    monkeypatch.setattr(
        backup_routes, "_require_permission", lambda request, permission: None
    )
    monkeypatch.setattr(
        backup_routes,
        "config",
        SimpleNamespace(
            database=SimpleNamespace(sync_dsn="postgresql://backup-target")
        ),
    )
    monkeypatch.setattr(backup_routes, "_run_subprocess", run_subprocess)

    response = await backup_export(_make_request(), format="sql")
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert response.status_code == 200
    assert body == sql_bytes
    run_subprocess.assert_awaited_once()


async def test_run_post_restore_migrations_runs_fix_and_upgrade(monkeypatch):
    calls: list[list[str]] = []

    async def fake_run_subprocess(cmd, *, timeout, cwd=None, input_bytes=None):
        calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(backup_routes, "_run_subprocess", fake_run_subprocess)

    ok, error = await _run_post_restore_migrations()

    assert ok is True
    assert error is None
    assert calls == [
        ["uv", "run", "python", "fix_alembic.py"],
        ["uv", "run", "alembic", "upgrade", "head"],
    ]


async def test_sync_deployment_url_settings_overwrites_stale_restore_urls(
    session, monkeypatch
):
    session.add_all(
        [
            BotSettings(key="panel_url", value="https://old.example.com/panel/"),
            BotSettings(key="admin_panel_url", value="https://old.example.com/panel/"),
            BotSettings(key="cabinet_url", value="https://old.example.com/cabinet/"),
        ]
    )
    await session.commit()

    monkeypatch.setattr(
        "app.services.bot_settings.config",
        SimpleNamespace(web=SimpleNamespace(site_url="https://new.example.com")),
    )

    updated = await sync_deployment_url_settings(session, overwrite_existing=True)
    await session.commit()

    assert updated == {
        "panel_url": "https://new.example.com/panel/",
        "admin_panel_url": "https://new.example.com/panel/",
        "cabinet_url": "https://new.example.com/cabinet/",
    }

    rows = {
        row.key: row.value
        for row in (
            await session.execute(
                select(BotSettings).where(BotSettings.key.in_(updated.keys()))
            )
        ).scalars()
    }
    assert rows == updated
