from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_current_admin
from app.schemas.promo import PromoCreate, PromoRead, PromoApply, PromoApplyResult
from app.services.promo import PromoService

router = APIRouter()


@router.get("/", response_model=list[PromoRead])
async def list_promos(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    return await PromoService(db).get_all()


@router.post("/", response_model=PromoRead, status_code=201)
async def create_promo(
    data: PromoCreate, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    promo = await PromoService(db).create(**data.model_dump())
    await db.commit()
    return promo


@router.delete("/{promo_id}", status_code=204)
async def delete_promo(
    promo_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    await PromoService(db).delete(promo_id)
    await db.commit()


@router.post("/{promo_id}/toggle", response_model=PromoRead)
async def toggle_promo(
    promo_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    promo = await PromoService(db).toggle_active(promo_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo not found")
    await db.commit()
    return promo


@router.post("/apply", response_model=PromoApplyResult)
async def apply_promo(
    data: PromoApply,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    promo = await PromoService(db).apply(data.code)
    if not promo:
        return PromoApplyResult(
            valid=False, message="Промокод недействителен или исчерпан"
        )
    await db.commit()
    return PromoApplyResult(
        valid=True,
        promo_type=promo.promo_type,
        value=promo.value,
        message="Промокод применён",
    )
