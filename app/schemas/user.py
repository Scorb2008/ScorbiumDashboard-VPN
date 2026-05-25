from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    id: int
    username: Optional[str] = None
    full_name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: Optional[str]
    full_name: str
    is_active: bool
    is_banned: bool
    balance: Optional[float] = 0.0
    referral_code: Optional[str] = None
    language: Optional[str] = None
    autorenew: bool = False
    created_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class UserUpdate(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    is_active: Optional[bool] = None
    is_banned: Optional[bool] = None


class UserDetail(UserRead):
    """Extended user info with subscriptions and payments count."""

    subscriptions_count: int = 0
    payments_count: int = 0
    vpn_keys_count: int = 0
