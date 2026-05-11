from fastapi import APIRouter


def get_dashboard_router() -> APIRouter:
    from app.api.dashboard.auth import router as auth_router
    from app.api.dashboard.views import router as views_router
    r = APIRouter()
    r.include_router(auth_router)
    r.include_router(views_router)
    return r
