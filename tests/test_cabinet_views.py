from pathlib import Path
from types import SimpleNamespace

from fastapi import Request
from fastapi.responses import Response

from app.api.cabinet.views import (
    _absolute_cabinet_url,
    _cabinet_redirect_url,
    _persist_cabinet_session,
)


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


def _make_request(
    *, cookie: str | None = None, forwarded_proto: str = "https"
) -> Request:
    headers = [(b"x-forwarded-proto", forwarded_proto.encode())]
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/cabinet/",
        "raw_path": b"/cabinet/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _make_request_with_forwarded_host(host: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/cabinet/",
        "raw_path": b"/cabinet/",
        "query_string": b"",
        "headers": [
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", host.encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _make_miniapp_request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/cabinet/profile",
        "raw_path": b"/cabinet/profile",
        "query_string": b"miniapp=1&tg_init_data=demo-init-data",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    return Request(scope)


def test_persist_cabinet_session_sets_cookie_for_authenticated_user():
    request = _make_request()
    response = Response()

    _persist_cabinet_session(request, response, _User(123))

    assert "cabinet_session=" in response.headers["set-cookie"]


def test_persist_cabinet_session_skips_existing_cookie():
    request = _make_request(cookie="cabinet_session=existing")
    response = Response()

    _persist_cabinet_session(request, response, _User(123))

    assert "set-cookie" not in response.headers


def test_cabinet_redirect_url_keeps_miniapp_context():
    request = _make_miniapp_request()

    redirect_url = _cabinet_redirect_url(request)

    assert redirect_url == "/cabinet/?miniapp=1&tg_init_data=demo-init-data"


def test_absolute_cabinet_url_keeps_forwarded_host_port():
    from app.core.config import config

    original_web_config = config.web_config
    monkeypatch_value = SimpleNamespace(site_url="")
    config.web_config = monkeypatch_value
    request = _make_request_with_forwarded_host("example.com:8443")

    try:
        absolute_url = _absolute_cabinet_url(
            request, "/cabinet/balance", payment_id=123
        )
    finally:
        config.web_config = original_web_config

    assert absolute_url == "https://example.com:8443/cabinet/balance?payment_id=123"


def test_absolute_cabinet_url_prefers_configured_site_url(monkeypatch):
    from app.core.config import config

    original_web_config = config.web_config
    monkeypatch.setattr(
        config,
        "web_config",
        SimpleNamespace(site_url="https://billing.example.com:9443"),
    )

    try:
        request = _make_request_with_forwarded_host("evil.example.com")
        absolute_url = _absolute_cabinet_url(
            request, "/cabinet/balance", payment_id=123
        )
    finally:
        monkeypatch.setattr(config, "web_config", original_web_config)

    assert (
        absolute_url
        == "https://billing.example.com:9443/cabinet/balance?payment_id=123"
    )


def test_absolute_cabinet_url_rejects_invalid_forwarded_host():
    from app.core.config import config

    original_web_config = config.web_config
    config.web_config = SimpleNamespace(site_url="")
    request = _make_request_with_forwarded_host("evil.example.com/bad path")

    try:
        absolute_url = _absolute_cabinet_url(
            request, "/cabinet/balance", payment_id=123
        )
    finally:
        config.web_config = original_web_config

    assert absolute_url == "https://testserver/cabinet/balance?payment_id=123"


def test_cabinet_login_keeps_web_login_visible_for_incomplete_telegram_hashes():
    project_root = Path(__file__).resolve().parents[1]
    base_template = (project_root / "app/templates/cabinet/base.html").read_text()
    login_template = (project_root / "app/templates/cabinet/login.html").read_text()
    cabinet_css = (project_root / "app/static/css/cabinet.css").read_text()

    assert "hashParams.has('tgWebAppPlatform')" not in base_template
    assert "getTelegramHashParam('tgWebAppPlatform')" not in base_template
    assert ".tg-miniapp-client #web-login" not in cabinet_css
    assert "setLoginState(m || 'Ошибка')" not in login_template
    assert "var initData = getMiniAppInitData();" in login_template


def test_cabinet_mobile_web_navigation_has_dedicated_fallback_menu():
    project_root = Path(__file__).resolve().parents[1]
    base_template = (project_root / "app/templates/cabinet/base.html").read_text()
    cabinet_css = (project_root / "app/static/css/cabinet.css").read_text()

    assert 'class="mnav"' in base_template
    assert "not is_log and not is_mini_app" in base_template
    assert 'href="/cabinet/promo"' in base_template
    assert 'href="/cabinet/support"' in base_template
    assert ".mnav {\n  position: fixed; left: 10px; right: 10px; bottom: 10px; z-index: 60;\n  display: none;" in cabinet_css
    assert "overflow-x: auto;" in cabinet_css
    assert ".mnav { display: flex; }" in cabinet_css
    assert ".wrap { padding-bottom: 94px; }" in cabinet_css
    assert "flex: 0 0 78px;" in cabinet_css
    assert ".mini-mode .mnav {\n  display: none !important;\n}" in cabinet_css
