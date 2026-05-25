from http.cookies import SimpleCookie

import pyotp
from fastapi import Request

from app.api.panel.routes.auth import (
    PREAUTH_COOKIE,
    logout,
    twofa_login_submit,
    login_submit,
)
from app.api.panel.routes.shared import SESSION_COOKIE, _get_admin_info
from app.core.config import config
from app.models.admin import Admin, AdminRole
from app.models.token_blacklist import BlacklistedToken
from app.services.admin_auth import authenticate_admin_credentials
from app.utils.security import create_access_token, hash_password


def _make_request(*, cookies: dict[str, str] | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookies:
        cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        headers.append((b"cookie", cookie_header.encode()))
    panel_login_path = config.web.panel_login_path
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": panel_login_path,
        "raw_path": panel_login_path.encode("utf-8"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _read_cookie(response, key: str) -> str | None:
    jar = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        jar.load(header)
    morsel = jar.get(key)
    return morsel.value if morsel else None


async def test_twofa_login_requires_preauth(session):
    admin = Admin(
        username="twofa-admin",
        password_hash=hash_password("StrongPass123"),
        role=AdminRole.SUPERADMIN.value,
        is_active=True,
        totp_secret=pyotp.random_base32(),
    )
    session.add(admin)
    await session.commit()

    code = pyotp.TOTP(admin.totp_secret, interval=30, digits=6).now()
    response = await twofa_login_submit(_make_request(), code=code, db=session)

    assert response.status_code == 200
    assert _read_cookie(response, SESSION_COOKIE) is None
    assert "Сначала введите логин и пароль" in response.body.decode("utf-8")


async def test_login_submit_sets_preauth_and_twofa_completes_login(session):
    secret = pyotp.random_base32()
    admin = Admin(
        username="secure-admin",
        password_hash=hash_password("StrongPass123"),
        role=AdminRole.SUPERADMIN.value,
        is_active=True,
        totp_secret=secret,
    )
    session.add(admin)
    await session.commit()

    first_response = await login_submit(
        _make_request(),
        username="secure-admin",
        password="StrongPass123",
        db=session,
    )
    preauth_token = _read_cookie(first_response, PREAUTH_COOKIE)

    assert first_response.status_code == 200
    assert preauth_token
    assert _read_cookie(first_response, SESSION_COOKIE) in (None, "")

    code = pyotp.TOTP(secret, interval=30, digits=6).now()
    second_response = await twofa_login_submit(
        _make_request(cookies={PREAUTH_COOKIE: preauth_token}),
        code=code,
        db=session,
    )

    assert second_response.status_code == 302
    assert _read_cookie(second_response, SESSION_COOKIE)


async def test_env_superadmin_login_upgrades_existing_operator_role(
    session, monkeypatch
):
    admin = Admin(
        username="root-admin",
        password_hash=hash_password("SomeOtherPass123"),
        role=AdminRole.OPERATOR.value,
        is_active=True,
    )
    session.add(admin)
    await session.commit()

    monkeypatch.setattr(
        "app.services.admin_auth.config",
        type(
            "Cfg",
            (),
            {
                "web": type(
                    "WebCfg",
                    (),
                    {
                        "web_superadmin_username": "root-admin",
                        "web_superadmin_password": type(
                            "Secret",
                            (),
                            {"get_secret_value": staticmethod(lambda: "RootPass123")},
                        )(),
                    },
                )(),
            },
        )(),
    )

    authenticated = await authenticate_admin_credentials(
        session,
        "root-admin",
        "RootPass123",
    )

    await session.refresh(admin)

    assert authenticated is not None
    assert authenticated.role == AdminRole.SUPERADMIN.value
    assert admin.role == AdminRole.SUPERADMIN.value


async def test_logout_blacklists_active_panel_session(session):
    token = create_access_token(subject="secure-admin", role=AdminRole.SUPERADMIN.value)
    response = await logout(
        _make_request(cookies={SESSION_COOKIE: token, PREAUTH_COOKIE: token}),
        db=session,
    )

    assert response.status_code == 302

    rows = await session.execute(
        BlacklistedToken.__table__.select().where(
            BlacklistedToken.sub == "secure-admin",
        )
    )
    entries = rows.fetchall()
    assert len(entries) == 2


def test_get_admin_info_rejects_revoked_panel_session():
    request = _make_request(cookies={SESSION_COOKIE: "token"})
    request.state.revoked_panel_session = True
    request.state.panel_admin_info = {
        "sub": "admin",
        "role": AdminRole.SUPERADMIN.value,
    }

    assert _get_admin_info(request) is None
