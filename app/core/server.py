import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.config import config
from app.core.database import init_db, close_db
from app.api.v1 import get_router
from app.api.panel import get_panel_router
from app.api.middleware import RateLimitMiddleware
from app.utils.log import log

_bot = None
_dp = None
_bg_tasks = []


def get_bot():
    return _bot


def get_dp():
    return _dp


def _start_bg_task(coro, name: str = ""):
    """Start a background task, store reference, and log exceptions."""
    task = asyncio.create_task(coro, name=name or None)
    _bg_tasks.append(task)
    task.add_done_callback(lambda t: _bg_tasks.remove(t) if t in _bg_tasks else None)
    task.add_done_callback(_log_task_exception)
    return task


def _log_task_exception(task: asyncio.Task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"Background task {task.get_name() or task} failed: {exc}", exc_info=exc)


def _make_dp():
    """
    Build a fresh Dispatcher every time.
    Routers are module-level singletons in aiogram 3 — once attached they
    cannot be re-attached to a new Dispatcher.  The only safe approach is to
    re-import the handler modules so Python re-executes them and creates brand
    new Router objects.
    """
    import importlib
    import sys
    from aiogram import Dispatcher
    from app.bot.middlewares import BanCheckMiddleware
    from app.bot.middlewares.throttle import ThrottleMiddleware
    from app.bot.middlewares.channel_check import ChannelCheckMiddleware
    from app.bot.middlewares.user_notify import UserNotifyMiddleware

    handler_modules = [
        "app.bot.handlers.start",
        "app.bot.handlers.buy",
        "app.bot.handlers.my_keys",
        "app.bot.handlers.payments",
        "app.bot.handlers.admin",
        "app.bot.handlers.profile",
        "app.bot.handlers.features",
        "app.bot.handlers.language",
        "app.bot.handlers.trial",
    ]

    for mod_name in handler_modules:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

    import app.bot.handlers.start as _start
    import app.bot.handlers.buy as _buy
    import app.bot.handlers.my_keys as _my_keys
    import app.bot.handlers.payments as _payments
    import app.bot.handlers.admin as _admin
    import app.bot.handlers.profile as _profile
    import app.bot.handlers.features as _features
    import app.bot.handlers.language as _language
    import app.bot.handlers.trial as _trial

    dp = Dispatcher()
    dp.update.outer_middleware(BanCheckMiddleware())
    dp.update.outer_middleware(ThrottleMiddleware())
    dp.update.outer_middleware(ChannelCheckMiddleware())
    dp.update.outer_middleware(UserNotifyMiddleware())
    from app.bot.middlewares.metrics import BotMetricsMiddleware
    dp.update.outer_middleware(BotMetricsMiddleware())
    dp.include_router(_start.router)
    dp.include_router(_buy.router)
    dp.include_router(_my_keys.router)
    dp.include_router(_payments.router)
    dp.include_router(_admin.router)
    dp.include_router(_profile.router)
    dp.include_router(_features.router)
    dp.include_router(_language.router)
    dp.include_router(_trial.router)
    return dp


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _bot, _dp

    log.info("🚀 Starting VPN Dashboard API...")

    import os as _os
    if not _os.environ.get("JWT_SECRET_KEY", "").strip():
        log.warning(
            "JWT_SECRET_KEY is not set! Authentication will fail. "
            "Generate one: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    await init_db()

    from aiogram import Bot
    from aiogram.enums import ParseMode
    from aiogram.client.default import DefaultBotProperties
    from app.tasks.payment_tasks import payment_polling_loop
    from app.tasks.vpn_tasks import expire_loop, sync_loop

    token = config.telegram.telegram_bot_token.get_secret_value()
    _bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _dp = _make_dp()

    mode = config.telegram.telegram_type_protocol

    if mode == "webhook":
        try:
            await _bot.set_webhook(
                url=config.telegram.telegram_webhook_url,
                allowed_updates=_dp.resolve_used_update_types(),
                drop_pending_updates=True,
            )
            log.info("Bot webhook set -> %s", config.telegram.telegram_webhook_url)
        except Exception as e:
            log.error("Failed to set Telegram webhook: %s. App will run without bot.", e)
    else:
        try:
            await _bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            log.warning("Failed to delete Telegram webhook (non-critical): %s", e)
        try:
            asyncio.create_task(
                _dp.start_polling(_bot, allowed_updates=_dp.resolve_used_update_types())
            )
            log.info("Bot polling started")
        except Exception as e:
            log.error("Failed to start Telegram polling: %s. App will run without bot.", e)

    _start_bg_task(payment_polling_loop(), name="payment_polling")
    _start_bg_task(expire_loop(), name="expire_loop")
    _start_bg_task(sync_loop(), name="sync_loop")

    from app.bot.middlewares.metrics import BotMetricsLoop
    _start_bg_task(BotMetricsLoop.run(), name="bot_metrics")

    from app.services.slow_query import register_slow_query_logger
    register_slow_query_logger()

    # Token blacklist cleanup every hour
    async def _token_cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                from app.core.database import AsyncSessionFactory
                from app.services.token_blacklist import TokenBlacklistService
                async with AsyncSessionFactory() as _s:
                    removed = await TokenBlacklistService(_s).cleanup_expired()
                    await _s.commit()
                    if removed:
                        log.info(f"Cleaned up {removed} expired blacklisted tokens")
            except Exception as e:
                log.error("token_cleanup error: %s", e, exc_info=True)
    _start_bg_task(_token_cleanup_loop(), name="token_cleanup")

    import os as _os
    _env_cryptobot = _os.environ.get("CRYPTOBOT_TOKEN", "").strip()
    if _env_cryptobot:
        from app.core.database import AsyncSessionFactory as _ASF
        from app.services.bot_settings import BotSettingsService as _BSS
        async with _ASF() as _s:
            _existing = await _BSS(_s).get("cryptobot_token")
            if not _existing:
                await _BSS(_s).set("cryptobot_token", _env_cryptobot)
                await _s.commit()
                log.info("✅ CryptoBot token seeded from .env")

    log.info("✅ Application ready")

    from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat
    user_commands = [
        BotCommand(command="start",      description="🏠 Главное меню"),
        BotCommand(command="profile",    description="👤 Мой профиль"),
        BotCommand(command="keys",       description="🔑 Мои подписки"),
        BotCommand(command="status",     description="📊 Статус подписок"),
        BotCommand(command="extend",     description="🔄 Продлить подписку"),
        BotCommand(command="top",        description="🏆 Топ рефереров"),
        BotCommand(command="gift",       description="🎁 Подарить подписку"),
        BotCommand(command="autorenew",  description="🔄 Автопродление"),
        BotCommand(command="id",         description="🆔 Мой Telegram ID"),
    ]
    admin_commands = user_commands + [
        BotCommand(command="admin",      description="👑 Панель администратора"),
        BotCommand(command="ban",        description="🚫 Забанить пользователя"),
        BotCommand(command="unban",      description="✅ Разбанить пользователя"),
        BotCommand(command="promo",      description="🎁 Создать промокод"),
        BotCommand(command="addbalance", description="💰 Пополнить баланс"),
        BotCommand(command="givekey",    description="🔑 Выдать ключ"),
    ]
    try:
        await _bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())
        for admin_id in config.telegram.telegram_admin_ids:
            try:
                await _bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception:
                pass
        log.info("✅ Bot commands set")
    except Exception as e:
        log.warning(f"Failed to set bot commands: {e}")
    yield

    log.info("🛑 Shutting down...")
    for task in list(_bg_tasks):
        if not task.done():
            task.cancel()
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks, return_exceptions=True)
        _bg_tasks.clear()

    try:
        if mode == "webhook":
            await _bot.delete_webhook()
        else:
            await _dp.stop_polling()
    except Exception as e:
        log.warning("Bot shutdown error (non-critical): %s", e)
    try:
        await _bot.session.close()
    except Exception as e:
        log.warning("Bot session close error (non-critical): %s", e)
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title=config.web.app_name,
        version=config.web.app_version,
        lifespan=_lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        redirect_slashes=False,
    )

    origins = [str(o) for o in config.web.allowed_origins] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware)

    # Sentry integration
    import os as _os
    _sentry_dsn = _os.environ.get("SENTRY_DSN", "").strip()
    if _sentry_dsn:
        import sentry_sdk
        sentry_sdk.init(
            dsn=_sentry_dsn,
            traces_sample_rate=0.1,
            environment=_os.environ.get("SENTRY_ENV", "production"),
        )
        log.info("Sentry initialized")

    from starlette.middleware.base import BaseHTTPMiddleware as _BHM
    from app.api.middleware.csrf import CSRFMiddleware, generate_csrf_token as _gct, CSRF_COOKIE as _CC

    class _CSRFInjector(_BHM):
        async def dispatch(self, request: Request, call_next):
            resp = await call_next(request)
            path = request.url.path
            if path.startswith("/panel") and not path.startswith("/panel/api"):
                if not request.cookies.get(_CC):
                    token = _gct()
                    resp.set_cookie(_CC, token, httponly=False, samesite="lax", max_age=86400)
            return resp
    app.add_middleware(_CSRFInjector)
    app.add_middleware(CSRFMiddleware)

    @app.exception_handler(Exception)
    async def _global_exc(request: Request, exc: Exception) -> JSONResponse:
        log.error("Unhandled exception on %s: %s", request.url, exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.exception_handler(403)
    async def _forbidden_exc(request: Request, exc: Exception):
        from fastapi.templating import Jinja2Templates
        tpl = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
        return tpl.TemplateResponse(
            "forbidden.html",
            {
                "request": request,
                "app_name": config.web.app_name,
                "app_version": config.web.app_version,
            },
            status_code=403,
        )

    class _SecurityHeaders(_BHM):
        async def dispatch(self, request: Request, call_next):
            resp = await call_next(request)
            # Standard headers
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

            path = request.url.path
            is_panel = path.startswith("/panel")
            is_miniapp = path.startswith("/app")
            is_docs = path in ("/docs", "/redoc", "/openapi.json")

            # X-Frame-Options
            if is_miniapp:
                # MiniApp runs in Telegram WebView iframe — must allow framing
                resp.headers["X-Frame-Options"] = "ALLOWALL"
            elif is_panel:
                resp.headers["X-Frame-Options"] = "SAMEORIGIN"
            else:
                resp.headers["X-Frame-Options"] = "DENY"

            # HSTS — only for HTTPS
            if request.url.scheme == "https":
                resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"

            # Permissions-Policy
            resp.headers["Permissions-Policy"] = (
                "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                "magnetometer=(), microphone=(), payment=(), usb=()"
            )

            # Content-Security-Policy
            if is_docs:
                # Swagger/Redoc need inline scripts/styles
                resp.headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                    "font-src 'self' https://fonts.gstatic.com; "
                    "img-src 'self' data:; "
                    "connect-src 'self'; "
                    "frame-src 'none'; "
                    "object-src 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )
            elif is_panel:
                # Panel uses HTMX + Bootstrap CDN
                resp.headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                    "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
                    "img-src 'self' data:; "
                    "connect-src 'self' ws: wss:; "
                    "frame-src 'none'; "
                    "object-src 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )
            elif is_miniapp:
                # MiniApp — inline styles + Telegram CDN + external APIs
                resp.headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://telegram.org; "
                    "style-src 'self' 'unsafe-inline'; "
                    "font-src 'self' data:; "
                    "img-src 'self' data: https:; "
                    "connect-src 'self' https:; "
                    "frame-src 'self' https://t.me; "
                    "object-src 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )

            # Remove server header
            if "server" in resp.headers:
                del resp.headers["server"]

            # Cache control for API
            if path.startswith("/api/") or path == "/metrics":
                resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                resp.headers["Pragma"] = "no-cache"

            return resp
    app.add_middleware(_SecurityHeaders)

    # Prometheus outermost — captures ALL requests including rate-limited/CSRF-blocked
    from app.api.middleware_prometheus import PrometheusMiddleware
    app.add_middleware(PrometheusMiddleware)

    app.include_router(get_router())
    app.include_router(get_panel_router())
    from app.api.miniapp import get_miniapp_router
    app.include_router(get_miniapp_router())


    static_path = Path(__file__).resolve().parent.parent / "static"
    static_path.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    @app.websocket("/ws/notifications", name="ws_notifications")
    async def ws_notifications(websocket: WebSocket):
        """Real-time notification stream for admin panel."""
        from app.services.notification import notification_manager
        from app.utils.security import decode_access_token_full

        token = websocket.query_params.get("token", "")
        info = decode_access_token_full(token) if token else None
        if not info:
            await websocket.close(code=4001)
            return

        await notification_manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except Exception:
            pass
        finally:
            await notification_manager.disconnect(websocket)

    @app.websocket("/ws/metrics")
    async def websocket_metrics(websocket: WebSocket):
        """WebSocket endpoint for real-time system metrics. Requires valid session cookie."""
        from app.utils.security import decode_access_token_full

        cookie = websocket.cookies.get("vpn_session")
        if not cookie:
            await websocket.close(code=4001)
            return
        admin_info = decode_access_token_full(cookie)
        if not admin_info:
            await websocket.close(code=4001)
            return
        await websocket.accept()
        try:
            while True:
                from app.services.system_metrics import SystemMetrics
                metrics = await SystemMetrics.collect()
                await websocket.send_json(metrics)
                await asyncio.sleep(3)
        except Exception:
            pass

    @app.get("/metrics-dashboard", include_in_schema=False)
    async def metrics_dashboard_page(request: Request):
        """Serve the HTML dashboard page."""
        from fastapi.templating import Jinja2Templates
        from pathlib import Path
        templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
        return templates.TemplateResponse("metrics/dashboard.html", {"request": request})

    @app.post(config.telegram.telegram_webhook_path, include_in_schema=False)
    async def telegram_webhook(request: Request):
        from aiogram.types import Update
        bot, dp = get_bot(), get_dp()
        if bot is None or dp is None:
            return JSONResponse({"ok": False}, status_code=503)
        update = Update.model_validate(await request.json())
        await dp.feed_update(bot, update)
        return JSONResponse({"ok": True})

    @app.get("/panel", include_in_schema=False)
    async def panel_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/panel/")

    @app.get("/panel-root", include_in_schema=False)
    async def panel_root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/panel/")

    @app.get("/health", include_in_schema=False)
    async def health_check():
        from sqlalchemy import text
        from fastapi.responses import JSONResponse
        try:
            from app.core.database import AsyncSessionFactory
            async with AsyncSessionFactory() as session:
                await session.execute(text("SELECT 1"))
            return JSONResponse({"status": "ok", "db": "connected"})
        except Exception as e:
            log.error("Health check failed: %s", e)
            return JSONResponse({"status": "error", "db": "unavailable"}, status_code=503)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics(request: Request):
        key = config.web.metrics_api_key.get_secret_value()
        if key:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {key}":
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        from app.services.metrics import metrics_response
        return metrics_response()

    return app


def _start_monitoring():
    """Start background service monitoring and alerts (called from lifespan)."""
    async def _monitor_loop():
        import asyncio
        from app.services.health import health_service
        from app.services.alerts import alert_manager
        from app.services.system_metrics import SystemMetrics

        log.info("🩺 Service monitor started")
        await asyncio.sleep(60)
        while True:
            await asyncio.sleep(60)
            try:
                await health_service.check_all()
                await health_service.send_alerts()

                # Check system metrics and alert
                metrics = await SystemMetrics.collect()
                await alert_manager.check_metrics_and_alert(metrics)

                # Check database health
                from app.core.database import AsyncSessionFactory
                from sqlalchemy import text
                try:
                    async with AsyncSessionFactory() as session:
                        await session.execute(text("SELECT 1"))
                    await alert_manager.check_service_health("Database", True)
                except Exception:
                    await alert_manager.check_service_health("Database", False)

            except Exception as e:
                log.error("Monitor loop error: %s", e)
    _start_bg_task(_monitor_loop(), name="service_monitor")

