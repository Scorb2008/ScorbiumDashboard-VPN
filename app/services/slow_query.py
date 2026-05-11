"""Slow query logger — SQLAlchemy event listener that logs queries exceeding a threshold."""
import time
from sqlalchemy import event
from sqlalchemy.engine import Engine
from app.utils.log import log

SLOW_QUERY_THRESHOLD = 1.0  


_slow_queries: list[dict] = []


@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_time", []).append(time.time())


@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total_time = time.time() - conn.info["query_start_time"].pop(-1)
    if total_time > SLOW_QUERY_THRESHOLD:
        from datetime import datetime, timezone
        from app.services.metrics import db_slow_queries_total, db_query_duration_seconds

        db_slow_queries_total.inc()
        db_query_duration_seconds.labels(operation="slow").observe(total_time)

        stmt_preview = statement[:200]
        if len(statement) > 200:
            stmt_preview += "..."

        log.warning(
            "⚠️ Slow query (%.2fs): %s", total_time, stmt_preview
        )

        _slow_queries.append({
            "time": datetime.now(timezone.utc),
            "query": statement,
            "duration": total_time,
        })
        if len(_slow_queries) > 100:
            _slow_queries.pop(0)


def get_slow_queries() -> list[dict]:
    """Return the in-memory slow query log."""
    return list(_slow_queries)


def register_slow_query_logger():
    """Register slow query event listeners on the engine."""
    log.info("Slow query logger registered (threshold: %.1fs)", SLOW_QUERY_THRESHOLD)
