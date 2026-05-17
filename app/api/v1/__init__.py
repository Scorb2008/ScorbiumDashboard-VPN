from fastapi import APIRouter

from .auth import router as auth_router
from .healthy import router as health_router
from .dashboard import router as dashboard_router
from .users import router as users_router
from .plans import router as plans_router
from .subscriptions import router as subscriptions_router
from .payments import router as payments_router
from .vpn import router as vpn_router
from .support import router as support_router
from .broadcasts import router as broadcasts_router
from .telegram import router as telegram_router
from .promos import router as promos_router
from .referrals import router as referrals_router


def get_router() -> APIRouter:
    api_router = APIRouter(prefix="/api/v1")

    api_router.include_router(health_router,        prefix="/health",        tags=["Health"])
    api_router.include_router(auth_router,          prefix="/auth",          tags=["Auth"])
    api_router.include_router(dashboard_router,     prefix="/dashboard",     tags=["Dashboard"])
    api_router.include_router(plans_router,         prefix="/plans",         tags=["Plans"])
    api_router.include_router(subscriptions_router, prefix="/subscriptions", tags=["Subscriptions"])
    api_router.include_router(payments_router,      prefix="/payments",      tags=["Payments"])
    api_router.include_router(vpn_router,           prefix="/vpn",           tags=["VPN"])
    api_router.include_router(support_router,       prefix="/support",       tags=["Support"])
    api_router.include_router(broadcasts_router,    prefix="/broadcasts",    tags=["Broadcasts"])
    api_router.include_router(telegram_router,      prefix="/telegram",      tags=["Telegram"])
    api_router.include_router(promos_router,        prefix="/promos",        tags=["Promos"])
    api_router.include_router(referrals_router,     prefix="/referrals",     tags=["Referrals"])
    api_router.include_router(users_router,         prefix="/users",         tags=["Users"])

    return api_router
