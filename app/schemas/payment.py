from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.models.payment import PaymentProvider, PaymentStatus


class PaymentCreate(BaseModel):
    user_id: int
    plan_id: int
    provider: PaymentProvider


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    vpn_key_id: Optional[int] = None
    provider: PaymentProvider
    payment_type: str = "subscription"
    external_id: Optional[str] = None
    amount: Decimal
    currency: str
    status: PaymentStatus
