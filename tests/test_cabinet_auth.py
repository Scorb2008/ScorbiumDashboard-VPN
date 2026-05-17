from unittest.mock import AsyncMock, patch

from fastapi import Request
from fastapi.responses import Response

from app.api.cabinet.auth import (
    _build_telegram_full_name,
    _extract_telegram_oidc_user_id,
    _is_secure_request,
    _parse_telegram_user_id,
    cabinet_auth,
    get_telegram_init_data,
    is_telegram_miniapp_request,
    set_session_cookie,
)


def _make_request(*, scheme: str = "http", forwarded_proto: str | None = None, query_string: bytes = b"") -> Request:
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
        "query_string": query_string,
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


def test_extract_telegram_oidc_user_id_uses_id_claim():
    payload = {"sub": "13693577412216818782", "id": 1980894188}
    assert _extract_telegram_oidc_user_id(payload) == 1980894188


def test_extract_telegram_oidc_user_id_rejects_out_of_range():
    payload = {"id": "13693577412216818782"}
    try:
        _extract_telegram_oidc_user_id(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for out-of-range Telegram user id")


def test_parse_telegram_user_id_rejects_zero_and_out_of_range():
    for raw_user_id in (0, "0", "13693577412216818782"):
        try:
            _parse_telegram_user_id(raw_user_id)
        except ValueError:
            continue
        raise AssertionError(f"Expected ValueError for invalid Telegram user id: {raw_user_id}")


def test_build_telegram_full_name_prefers_split_names():
    assert _build_telegram_full_name("Scorb", "Dev", "Ignored Name") == "Scorb Dev"


def test_build_telegram_full_name_uses_fallback_name():
    assert _build_telegram_full_name("", "", "Fallback User") == "Fallback User"


def test_get_telegram_init_data_uses_query_fallback():
    request = _make_request(query_string=b"tg_init_data=test-init-data")
    assert get_telegram_init_data(request) == "test-init-data"


def test_is_telegram_miniapp_request_uses_miniapp_flag():
    request = _make_request(query_string=b"miniapp=1")
    assert is_telegram_miniapp_request(request) is True


async def test_cabinet_auth_oidc_uses_numeric_telegram_id(session):
    request = _make_request(scheme="https")
    request._json = {"id_token": "test-token"}

    async def _json():
        return request._json

    request.json = _json

    with patch(
        "app.api.cabinet.auth.verify_telegram_id_token",
        AsyncMock(
            return_value={
                "sub": "13693577412216818782",
                "id": 1980894188,
                "preferred_username": "scorb_user",
                "name": "Scorb Dev",
            }
        ),
    ):
        response = await cabinet_auth(request, db=session)

    assert response.status_code == 200
    assert b'"ok":true' in response.body


async def test_cabinet_auth_miniapp_redirects_back_to_miniapp(session):
    request = _make_request(scheme="https")
    request._json = {"initData": "test-init-data"}

    async def _json():
        return request._json

    request.json = _json

    with patch(
        "app.api.cabinet.auth._verify_telegram_init_data",
        return_value={
            "user": '{"id": 1980894188, "username": "scorb_user", "first_name": "Scorb"}',
        },
    ):
        response = await cabinet_auth(request, db=session)

    assert response.status_code == 200
    assert b'"redirect":"/cabinet/?miniapp=1"' in response.body
