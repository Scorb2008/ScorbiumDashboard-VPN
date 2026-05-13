from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db

router = APIRouter()


@router.get("/", summary="Health check")
async def healthy(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception:
        return JSONResponse(
            {"status": "error", "db": "unavailable"},
            status_code=503,
        )
