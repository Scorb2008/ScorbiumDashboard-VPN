from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.models.broadcast import BroadcastStatus


class BroadcastCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    text: str = Field(..., min_length=1)
    target: Literal["all", "active", "expired"] = "all"
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2"] = "HTML"


class BroadcastRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    text: str
    target: str
    parse_mode: str
    status: BroadcastStatus
    sent_count: int
    failed_count: int
