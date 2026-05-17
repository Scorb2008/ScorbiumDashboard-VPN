from fastapi import APIRouter
from .routes import router as routes_router


def get_panel_router() -> APIRouter:
    panel = APIRouter(prefix="/panel")
    panel.include_router(routes_router)
    return panel
