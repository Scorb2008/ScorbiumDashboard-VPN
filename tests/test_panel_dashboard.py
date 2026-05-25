from fastapi import Request

from app.api.panel.routes.dashboard import dashboard
from app.core.config import config


def _make_request() -> Request:
    panel_root = config.web.panel_root
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": panel_root,
        "raw_path": panel_root.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


async def test_dashboard_renders_actual_admin_role(session, monkeypatch):
    admin_info = {"sub": "root-admin", "role": "superadmin"}

    monkeypatch.setattr(
        "app.api.panel.routes.dashboard._require_permission",
        lambda request, permission: admin_info,
    )
    monkeypatch.setattr(
        "app.services.pasarguard.pasarguard.get_vpn_panel",
        lambda: type(
            "FakePanel",
            (),
            {"get_system_stats": staticmethod(lambda: {"status": "ok"})},
        )(),
    )
    monkeypatch.setattr(
        "app.services.system_metrics.SystemMetrics.collect",
        staticmethod(lambda: {"cpu_percent": 0}),
    )

    response = await dashboard(_make_request(), db=session)
    html = response.body.decode("utf-8")

    assert "root-admin" in html
    assert "Роль: superadmin" in html
