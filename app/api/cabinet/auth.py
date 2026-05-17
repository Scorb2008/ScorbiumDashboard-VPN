import hashlib
import hmac
import json
from datetime import timedelta, datetime, timezone
from urllib.parse import unquote_plus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.schemas.user import UserCreate
from app.services.user import UserService
from app.utils.log import log
from app.utils.security import create_access_token, decode_access_token_full
from app.utils.telegram_oidc import verify_telegram_id_token

router = APIRouter()

COOKIE_NAME = "cabinet_session"
CABINET_COOKIE_MAX_AGE = 86400 * 30
INT64_MAX = 2**63 - 1
MINIAPP_INIT_DATA_PARAM = "tg_init_data"
MINIAPP_MODE_PARAM = "miniapp"


def _is_secure_request(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def get_telegram_init_data(request: Request) -> str:
    return (
        request.headers.get("X-Telegram-Init-Data", "")
        or request.query_params.get(MINIAPP_INIT_DATA_PARAM, "")
        or request.query_params.get("initData", "")
    )


def is_telegram_miniapp_request(request: Request) -> bool:
    miniapp_flag = request.query_params.get(MINIAPP_MODE_PARAM, "").strip().lower()
    return bool(
        get_telegram_init_data(request)
        or miniapp_flag in {"1", "true", "yes", "on"}
    )


def _build_telegram_full_name(
    first_name: str | None = None,
    last_name: str | None = None,
    fallback_name: str | None = None,
) -> str:
    full_name = " ".join(filter(None, [(first_name or "").strip(), (last_name or "").strip()])).strip()
    if full_name:
        return full_name
    return (fallback_name or "").strip() or "Telegram User"


def _verify_telegram_init_data(init_data: str) -> dict | None:
    try:
        bot_token = config.telegram.telegram_bot_token.get_secret_value()
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

        params = {}
        for part in init_data.split("&"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            params[k] = unquote_plus(v)

        hash_val = params.pop("hash", "")
        if not hash_val:
            return None

        items = sorted(params.items(), key=lambda x: x[0])
        data_check_string = "\n".join(f"{k}={v}" for k, v in items)

        computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, hash_val):
            return None

        auth_date = int(params.get("auth_date", 0))
        if datetime.now(timezone.utc).timestamp() - auth_date > 86400:
            return None

        return params
    except Exception as e:
        log.error("MiniApp init data verification failed: %s", e)
        return None


def _verify_telegram_login(data: dict) -> dict | None:
    try:
        bot_token = config.telegram.telegram_bot_token.get_secret_value()
        secret_key = hashlib.sha256(bot_token.encode()).digest()

        data = dict(data)
        hash_val = data.pop("hash", "")
        if not hash_val:
            return None

        items = sorted(data.items(), key=lambda x: x[0])
        data_check_string = "\n".join(f"{k}={v}" for k, v in items)

        computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, hash_val):
            return None

        auth_date = int(data.get("auth_date", 0))
        if datetime.now(timezone.utc).timestamp() - auth_date > 86400:
            return None

        return data
    except Exception as e:
        log.error("TG Login Widget verification failed: %s", e)
        return None


async def get_cabinet_user(request: Request, db: AsyncSession):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        payload = decode_access_token_full(token)
        if payload:
            try:
                from app.services.token_blacklist import TokenBlacklistService
                jti = payload.get("jti", "")
                sub = payload.get("sub", "")
                if await TokenBlacklistService(db).is_blacklisted(jti, sub):
                    return None
                user_id = int(payload["sub"])
                return await UserService(db).get_by_id(user_id)
            except (ValueError, TypeError):
                pass
    return None


async def try_miniapp_auth(request: Request, db: AsyncSession):
    init_data = get_telegram_init_data(request)
    if not init_data:
        return None
    tg_data = _verify_telegram_init_data(init_data)
    if not tg_data:
        return None
    try:
        user_info = json.loads(tg_data.get("user", "{}"))
        if not user_info.get("id"):
            return None
        user_id = _parse_telegram_user_id(user_info["id"])
        user, _ = await UserService(db).sync_telegram_profile(UserCreate(
            id=user_id,
            username=user_info.get("username", ""),
            full_name=_build_telegram_full_name(
                user_info.get("first_name", ""),
                user_info.get("last_name", ""),
                user_info.get("name", ""),
            ),
        ))
        return user
    except Exception as e:
        log.error("MiniApp auto-auth error: %s", e)
        return None


def set_session_cookie(resp, user_id: int, *, secure: bool):
    token = create_access_token(subject=str(user_id), role="user", expires_delta=timedelta(days=30))
    resp.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite="lax", secure=secure, max_age=CABINET_COOKIE_MAX_AGE, path="/",
    )


def _extract_telegram_oidc_user_id(payload: dict) -> int:
    """Extract Telegram numeric user id from OIDC payload.

    Telegram OIDC exposes the chat user id in `id`, while `sub` is the OIDC
    subject and may exceed BIGINT. Persist only the Telegram numeric id.
    """
    raw_user_id = payload.get("id", 0)
    return _parse_telegram_user_id(raw_user_id)


def _parse_telegram_user_id(raw_user_id) -> int:
    user_id = int(raw_user_id)
    if user_id <= 0 or user_id > INT64_MAX:
        raise ValueError("Invalid Telegram user id")
    return user_id


@router.post("/cabinet/auth")
async def cabinet_auth(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        body = dict(await request.form())

    id_token = body.get("id_token", "") if isinstance(body, dict) else ""

    if id_token:
        payload = await verify_telegram_id_token(id_token)
        if not payload:
            return JSONResponse({"ok": False, "message": "Auth verification failed"}, status_code=401)
        try:
            user_id = _extract_telegram_oidc_user_id(payload)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "message": "Invalid user ID"}, status_code=401)
        user_info = {
            "username": payload.get("preferred_username", ""),
            "first_name": (payload.get("name", "") or "").split(" ", 1)[0],
            "last_name": (payload.get("name", "") or "").split(" ", 1)[1] if " " in (payload.get("name", "") or "") else "",
        }
        user, _ = await UserService(db).sync_telegram_profile(UserCreate(
            id=user_id,
            username=user_info["username"],
            full_name=_build_telegram_full_name(
                user_info["first_name"],
                user_info["last_name"],
                payload.get("name", ""),
            ),
        ))
        if user.is_banned:
            return JSONResponse({"ok": False, "message": "Account is banned"}, status_code=403)
        resp = JSONResponse({"ok": True, "redirect": "/cabinet/"})
        set_session_cookie(resp, user.id, secure=_is_secure_request(request))
        return resp

    init_data = body.get("initData", "") if isinstance(body, dict) else ""
    if init_data:
        tg_data = _verify_telegram_init_data(init_data)
        if tg_data and tg_data.get("user"):
            try:
                user_info = json.loads(tg_data["user"])
                user_id = _parse_telegram_user_id(user_info.get("id", 0))
            except (ValueError, TypeError, json.JSONDecodeError):
                return JSONResponse({"ok": False, "message": "Invalid user data"}, status_code=401)
        else:
            return JSONResponse({"ok": False, "message": "Auth verification failed"}, status_code=401)
    else:
        tg_data = _verify_telegram_login(body)
        if not tg_data:
            return JSONResponse({"ok": False, "message": "Auth verification failed"}, status_code=401)
        try:
            user_id = _parse_telegram_user_id(tg_data.get("id", 0))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "message": "Invalid user ID"}, status_code=401)
        user_info = tg_data

    if not user_id:
        return JSONResponse({"ok": False, "message": "Invalid user ID"}, status_code=401)

    user, _ = await UserService(db).sync_telegram_profile(UserCreate(
        id=user_id,
        username=user_info.get("username", ""),
        full_name=_build_telegram_full_name(
            user_info.get("first_name", ""),
            user_info.get("last_name", ""),
            user_info.get("name", ""),
        ),
    ))
    if user.is_banned:
        return JSONResponse({"ok": False, "message": "Account is banned"}, status_code=403)

    redirect_path = "/cabinet/?miniapp=1" if init_data else "/cabinet/"
    resp = JSONResponse({"ok": True, "redirect": redirect_path})
    set_session_cookie(resp, user.id, secure=_is_secure_request(request))
    return resp
