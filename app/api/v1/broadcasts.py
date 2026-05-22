from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_current_admin
from app.schemas.broadcast import BroadcastCreate, BroadcastRead
from app.services.broadcast import BroadcastService

router = APIRouter()


@router.get("/", response_model=list[BroadcastRead], summary="List broadcasts")
async def list_broadcasts(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[BroadcastRead]:
    return await BroadcastService(db).get_all(limit=limit, offset=offset)


@router.get("/{broadcast_id}", response_model=BroadcastRead, summary="Get broadcast")
async def get_broadcast(
    broadcast_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> BroadcastRead:
    bc = await BroadcastService(db).get_by_id(broadcast_id)
    if not bc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Broadcast not found"
        )
    return bc


@router.post(
    "/",
    response_model=BroadcastRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create broadcast draft",
)
async def create_broadcast(
    data: BroadcastCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> BroadcastRead:
    return await BroadcastService(db).create(**data.model_dump())


@router.post(
    "/{broadcast_id}/send", response_model=BroadcastRead, summary="Send broadcast"
)
async def send_broadcast(
    broadcast_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> BroadcastRead:
    bc = await BroadcastService(db).send(broadcast_id)
    if not bc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Broadcast not found or already sent",
        )
    return bc
