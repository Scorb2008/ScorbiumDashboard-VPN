"""Panel route modules — split from monolithic views.py for maintainability."""
from fastapi import APIRouter

from . import auth, dashboard, users, plans, payments
from . import subscriptions, promos, referrals, support, vpn
from . import broadcasts, telegram, backup, pasarguard, nodes
from . import exports, admins, keyboard, audit, monitoring, notifications

router = APIRouter()
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(plans.router, prefix="/plans")
router.include_router(payments.router, prefix="/payments")
router.include_router(subscriptions.router, prefix="/subscriptions")
router.include_router(promos.router, prefix="/promos")
router.include_router(referrals.router, prefix="/referrals")
router.include_router(support.router, prefix="/support")
router.include_router(vpn.router, prefix="/vpn")
router.include_router(broadcasts.router, prefix="/broadcasts")
router.include_router(telegram.router, prefix="/telegram")
router.include_router(backup.router, prefix="/backup")
router.include_router(pasarguard.router, prefix="/pasarguard")
router.include_router(nodes.router, prefix="/nodes")
router.include_router(exports.router, prefix="/exports")
router.include_router(admins.router, prefix="/admins")
router.include_router(keyboard.router, prefix="/keyboard")
router.include_router(audit.router, prefix="/audit")
router.include_router(monitoring.router, prefix="/monitoring")
router.include_router(notifications.router, prefix="/notifications")
router.include_router(users.router, prefix="/users")
