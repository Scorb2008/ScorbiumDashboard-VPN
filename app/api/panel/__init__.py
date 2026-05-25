from fastapi import APIRouter

from app.core.config import config
from .routes import router as routes_router


def get_panel_router() -> APIRouter:
    panel = APIRouter(prefix=config.web.panel_prefix)
    panel.include_router(routes_router, tags=["Admin Panel"])
    return panel
