from fastapi import Request
from fastapi.responses import Response

from app.api.cabinet.views import _cabinet_redirect_url, _persist_cabinet_session


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


def _make_request(*, cookie: str | None = None, forwarded_proto: str = "https") -> Request:
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
