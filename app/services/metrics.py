"""Prometheus metrics collection for the VPN dashboard."""

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

REGISTRY = CollectorRegistry()

# ── HTTP metrics ──
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=REGISTRY,
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently in progress",
    ["method"],
    registry=REGISTRY,
)

# ── DB metrics ──
db_connections_active = Gauge(
    "db_connections_active",
    "Active database connections",
    registry=REGISTRY,
)

db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    registry=REGISTRY,
)

db_slow_queries_total = Counter(
    "db_slow_queries_total",
    "Total slow database queries (>1s)",
    registry=REGISTRY,
)

# ── Business metrics ──
active_users = Gauge(
    "active_users_total",
    "Total active users",
    registry=REGISTRY,
)

active_subscriptions = Gauge(
    "active_subscriptions_total",
    "Total active VPN subscriptions",
    registry=REGISTRY,
)

expired_subscriptions = Gauge(
    "expired_subscriptions_total",
    "Total expired VPN subscriptions",
    registry=REGISTRY,
)

revenue_total = Gauge(
    "revenue_total_rub",
    "Total revenue in RUB",
    registry=REGISTRY,
)

pending_payments = Gauge(
    "pending_payments_total",
    "Total pending payments",
    registry=REGISTRY,
)

# ── Bot metrics ──
bot_messages_sent_total = Counter(
    "bot_messages_sent_total",
    "Total messages sent by bot",
    ["status"],
    registry=REGISTRY,
)

bot_messages_received_total = Counter(
    "bot_messages_received_total",
    "Total messages received by bot",
    ["command"],
    registry=REGISTRY,
)

bot_handler_duration_seconds = Histogram(
    "bot_handler_duration_seconds",
    "Bot handler execution time",
    ["handler"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    registry=REGISTRY,
)

bot_users_online = Gauge(
    "bot_users_online",
    "Bot users online (seen in last 5 min)",
    registry=REGISTRY,
)

bot_messages_per_second = Gauge(
    "bot_messages_per_second",
    "Messages per second (1-min moving avg)",
    registry=REGISTRY,
)

# ── Service health gauges ──
service_health = Gauge(
    "service_health_status",
    "Service health status (1=healthy, 0=down)",
    ["service"],
    registry=REGISTRY,
)

service_response_time = Gauge(
    "service_response_time_seconds",
    "Service last response time in seconds",
    ["service"],
    registry=REGISTRY,
)

# ── Payment metrics ──
payments_total = Counter(
    "payments_total",
    "Total payments processed",
    ["provider", "status"],
    registry=REGISTRY,
)

payment_amount_total = Counter(
    "payment_amount_total_rub",
    "Total payment amount in RUB",
    ["provider"],
    registry=REGISTRY,
)

# ── Background task metrics ──
bg_task_duration_seconds = Histogram(
    "bg_task_duration_seconds",
    "Background task execution time",
    ["task_name"],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
    registry=REGISTRY,
)

bg_task_errors_total = Counter(
    "bg_task_errors_total",
    "Total background task errors",
    ["task_name"],
    registry=REGISTRY,
)


def metrics_response():
    """Return a FastAPI-compatible response with Prometheus metrics."""
    from fastapi.responses import Response

    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )
