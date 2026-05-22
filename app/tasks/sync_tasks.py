"""Backward-compatible sync task entrypoints.

Historically the panel imported ``sync_all_users`` from this module.
The implementation now lives in ``app.tasks.vpn_tasks``; keep this shim
so older call sites keep working after deploys.
"""

from app.tasks.vpn_tasks import sync_keys_from_marzban


async def sync_all_users() -> None:
    """Compatibility wrapper for legacy imports."""
    await sync_keys_from_marzban()
