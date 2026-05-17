"""
Panel views — now a thin re-export of the modular route packages.

All route logic has been moved to app/api/panel/routes/:
  - auth.py          (login, 2FA, Mini App login)
  - dashboard.py     (main dashboard)
  - users.py         (user management)
  - plans.py         (subscription plan management)
  - payments.py      (payment management + stats)
  - subscriptions.py (VPN key/subscription management)
  - promos.py        (promo code management)
  - referrals.py     (referral system)
  - support.py       (support tickets)
  - vpn.py           (VPN keys: revoke, extend, delete, sync)
  - broadcasts.py    (broadcast messages)
  - telegram.py      (bot settings, payment system config)
  - backup.py        (DB backup/restore)
  - pasarguard.py    (Marzban/Pasarguard panel)
  - nodes.py         (VPN nodes management)
  - exports.py       (CSV/XLSX export)
  - admins.py        (admin user management)
  - keyboard.py      (bot keyboard layout editor)
  - audit.py         (audit log viewer)
  - monitoring.py    (system health & monitoring)
  - notifications.py (notification settings & testing)
  - shared.py        (shared utilities, templates, auth helpers)

To add new endpoints, create a new file in routes/ and add it to routes/__init__.py.
"""
from .routes import router

__all__ = ["router"]
