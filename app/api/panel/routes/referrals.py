"""Referral system routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.referral import ReferralService

from .shared import _require_permission, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def referrals_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "referrals")
    ctx = await _base_ctx(request, db, "referrals")
    ctx["top_referrers"] = await ReferralService(db).get_top_referrers(limit=50)
    ctx["stats"] = await ReferralService(db).get_stats()
    return templates.TemplateResponse("referrals.html", ctx)
