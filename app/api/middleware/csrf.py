import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import config

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_LENGTH = 48

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_SAFE_PATHS = {
    config.web.panel_login_path,
    f"{config.web.panel_login_path}/",
    config.web.panel_path("login-2fa"),
    config.web.panel_path("logout"),
    config.web.panel_path("2fa-login"),
    config.web.panel_path("ws-token"),
    f"{config.web.panel_path('ws-token')}/",
    "/cabinet/auth",
    "/cabinet/auth/",
    "/health",
}

_CABINET_PUBLIC_PATHS = {
    "/cabinet/auth",
    "/cabinet/auth/",
    "/cabinet/logout",
    "/cabinet/logout/",
}


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(CSRF_LENGTH)


def _should_skip(request: Request) -> bool:
    path = request.url.path
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if path in _SAFE_PATHS:
        return True
    if path.startswith("/api/v1/"):
        return True
    if path.startswith("/webhook/"):
        return True
    if path.startswith("/static/"):
        return True
    if path in _CABINET_PUBLIC_PATHS:
        return True
    if (
        path.startswith("/docs")
        or path.startswith("/redoc")
        or path.startswith("/openapi")
    ):
        return True
    return False


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double Submit Cookie CSRF protection for panel routes."""

    async def dispatch(self, request: Request, call_next):
        if _should_skip(request):
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE)
        header_token = request.headers.get(CSRF_HEADER)

        if not cookie_token or not header_token or cookie_token != header_token:
            is_htmx = request.headers.get("HX-Request") == "true"
            if is_htmx:
                import json

                resp = Response(status_code=403)
                resp.headers["HX-Trigger"] = json.dumps(
                    {"showToast": {"msg": "Error CSRF. Update Page.", "type": "error"}}
                )
                return resp
            if request.headers.get("accept", "").startswith("application/json"):
                from starlette.responses import JSONResponse

                return JSONResponse(
                    {"detail": "CSRF token missing or invalid"},
                    status_code=403,
                )
            return Response(status_code=403)

        return await call_next(request)
