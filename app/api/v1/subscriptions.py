from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_admin, get_db
from app.schemas.vpn import VpnKeyRead
from app.services.vpn_key import VpnKeyService

router = APIRouter()


@router.get(
    "/", response_model=list[VpnKeyRead], summary="List all VPN keys (subscriptions)"
)
async def list_subscriptions(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[VpnKeyRead]:
    return await VpnKeyService(db).get_all(limit=limit, offset=offset)


@router.get("/{key_id}", response_model=VpnKeyRead, summary="Get VPN key")
async def get_subscription(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> VpnKeyRead:
    key = await VpnKeyService(db).get_by_id(key_id)
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return key


@router.post("/{key_id}/cancel", response_model=VpnKeyRead, summary="Revoke VPN key")
async def cancel_subscription(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> VpnKeyRead:
    key = await VpnKeyService(db).revoke(key_id)
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.commit()
    return key


@router.post("/expire-outdated", summary="Expire all outdated VPN keys")
async def expire_outdated(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> dict:
    count = await VpnKeyService(db).expire_outdated()
    await db.commit()
    return {"expired": count}
