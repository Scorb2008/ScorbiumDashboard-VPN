from fastapi import APIRouter
from .views import router as miniapp_router


def get_miniapp_router() -> APIRouter:
    r = APIRouter()
    r.include_router(miniapp_router, prefix="/app")
    return r
