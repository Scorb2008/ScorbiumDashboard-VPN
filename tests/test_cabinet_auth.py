from fastapi import Request
from fastapi.responses import Response

from app.api.cabinet.auth import _is_secure_request, set_session_cookie


def _make_request(*, scheme: str = "http", forwarded_proto: str | None = None) -> Request:
    headers = []
    if forwarded_proto is not None:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": "/cabinet/",
        "raw_path": b"/cabinet/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_is_secure_request_uses_scheme():
    request = _make_request(scheme="https")
    assert _is_secure_request(request) is True


def test_is_secure_request_prefers_forwarded_proto():
    request = _make_request(scheme="http", forwarded_proto="https")
    assert _is_secure_request(request) is True


def test_set_session_cookie_allows_insecure_local_cookie():
    response = Response()
    set_session_cookie(response, 123, secure=False)

    cookie_header = response.headers["set-cookie"]
    assert "cabinet_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Secure" not in cookie_header


def test_set_session_cookie_sets_secure_flag():
    response = Response()
    set_session_cookie(response, 123, secure=True)

    cookie_header = response.headers["set-cookie"]
    assert "Secure" in cookie_header
