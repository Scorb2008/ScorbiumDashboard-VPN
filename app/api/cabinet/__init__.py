from fastapi import APIRouter


def get_cabinet_router() -> APIRouter:
    from app.api.cabinet.auth import router as auth_router
    from app.api.cabinet.views import router as views_router
    r = APIRouter()
    r.include_router(auth_router, tags=["Cabinet Auth"])
    r.include_router(views_router, tags=["Cabinet"])
    return r
