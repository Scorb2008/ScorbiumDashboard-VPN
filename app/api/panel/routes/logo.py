"""Logo upload/clear endpoints for panel settings."""
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.branding_asset import BrandingAssetService

from .shared import _require_permission

router = APIRouter()

MAX_LOGO_SIZE = 2 * 1024 * 1024  

_IMAGE_MAGIC: dict[bytes, tuple[str, str]] = {
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpeg", "image/jpeg"),
    b"GIF87a": ("gif", "image/gif"),
    b"GIF89a": ("gif", "image/gif"),
    b"RIFF": ("webp", "image/webp"),
}


def _detect_image(data: bytes) -> tuple[str, str] | None:
    for magic, (ext, mime) in _IMAGE_MAGIC.items():
        if data.startswith(magic):
            return ext, mime
    return None


@router.post("/logo/upload")
async def logo_upload(
    request: Request,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")

    raw = await logo.read()
    if len(raw) > MAX_LOGO_SIZE:
        return JSONResponse({"ok": False, "message": "Файл больше 2MB"}, status_code=400)

    detected = _detect_image(raw)
    if not detected:
        return JSONResponse({"ok": False, "message": "Допустимы: PNG, JPG, WebP, GIF"}, status_code=400)

    _ext, mime = detected
    svc = BrandingAssetService(db)
    await svc.save_logo(raw, mime)
    await db.commit()

    return JSONResponse({"ok": True})


@router.post("/logo/clear")
async def logo_clear(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")

    svc = BrandingAssetService(db)
    await svc.clear_logo()
    await db.commit()

    return JSONResponse({"ok": True})


@router.get("/logo/current")
async def logo_current(db: AsyncSession = Depends(get_db)):
    payload = await BrandingAssetService(db).get_logo_payload()
    if not payload:
        return Response(status_code=404)

    mime_type, raw = payload
    return Response(
        content=raw,
        media_type=mime_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
