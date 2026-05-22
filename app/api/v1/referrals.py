from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_current_admin
from app.services.referral import ReferralService

router = APIRouter()


@router.get("/stats")
async def referral_stats(
    db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    return await ReferralService(db).get_stats()


@router.get("/top")
async def referral_top(
    limit: int = 20, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    return await ReferralService(db).get_top(limit=limit)


@router.get("/user/{user_id}")
async def user_referrals(
    user_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    svc = ReferralService(db)
    refs = await svc.get_for_user(user_id)
    return refs
