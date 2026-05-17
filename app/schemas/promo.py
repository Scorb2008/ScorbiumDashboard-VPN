from decimal import Decimal
from typing import Optional
from pydantic import BaseModel

from app.models.promo import PromoType


class PromoCreate(BaseModel):
    code: str
    promo_type: PromoType
    value: Decimal
    plan_id: Optional[int] = None
    max_uses: int = 0


class PromoRead(BaseModel):
    id: int
    code: str
    promo_type: PromoType
    value: Decimal
    plan_id: Optional[int] = None
    max_uses: int
    current_uses: int
    is_active: bool

    model_config = {"from_attributes": True}


class PromoApply(BaseModel):
    code: str
    user_id: Optional[int] = None


class PromoApplyResult(BaseModel):
    valid: bool
    promo_type: Optional[PromoType] = None
    value: Optional[Decimal] = None
    message: str
