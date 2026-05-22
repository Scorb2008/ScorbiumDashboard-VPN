from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_current_admin
from app.schemas.vpn import VpnKeyRead
from app.services.vpn_key import VpnKeyService

router = APIRouter()


@router.get("/{user_id}/keys", response_model=list[VpnKeyRead])
async def get_user_keys(
    user_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    return await VpnKeyService(db).get_user_keys(user_id)


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    key = await VpnKeyService(db).revoke(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    await db.commit()
    return {"detail": f"Key {key_id} revoked"}


@router.delete("/keys/{key_id}/delete")
async def delete_key(
    key_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):

    key = await VpnKeyService(db).delete_from_marzban(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    await db.commit()
    return {"detail": f"Key {key_id} deleted from Marzban"}


@router.post("/sync")
async def sync_keys(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    result = await VpnKeyService(db).sync_from_marzban()
    await db.commit()
    return result
