"""Prometheus middleware for FastAPI — tracks HTTP request metrics."""
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.services.metrics import (
    http_requests_total,
    http_request_duration_seconds,
    http_requests_in_progress,
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = self._normalize_path(request.url.path)

        # Skip metrics endpoint itself
        if path == "/metrics":
            return await call_next(request)

        http_requests_in_progress.labels(method=method).inc()
        start = time.time()

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            duration = time.time() - start
            http_requests_total.labels(
                method=method, endpoint=path, status=status
            ).inc()
            http_request_duration_seconds.labels(
                method=method, endpoint=path
            ).observe(duration)
            http_requests_in_progress.labels(method=method).dec()

        return response

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Replace numeric path segments with {id} to reduce cardinality."""
        import re
        return re.sub(r"/\d+", "/{id}", path)
