from starlette.requests import Request

from app.api.middleware.csrf import _should_skip
from app.core.config import config


def _make_request(path: str, method: str = "POST") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    return Request(scope)


def test_should_skip_cabinet_auth_post():
    assert _should_skip(_make_request("/cabinet/auth")) is True


def test_should_protect_other_cabinet_posts():
    assert _should_skip(_make_request("/cabinet/support/create")) is False


def test_should_not_skip_panel_post():
    assert _should_skip(_make_request(config.web.panel_path("telegram/send"))) is False


def test_should_skip_cabinet_get():
    assert _should_skip(_make_request("/cabinet/support", method="GET")) is True
