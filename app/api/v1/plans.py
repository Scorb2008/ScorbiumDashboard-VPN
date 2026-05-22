from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_current_admin
from app.schemas.plan import PlanCreate, PlanRead, PlanUpdate
from app.services.plan import PlanService

router = APIRouter()


@router.get("/", response_model=list[PlanRead], summary="List all plans")
async def list_plans(
    only_active: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[PlanRead]:
    """Public endpoint — used by bot to show available plans."""
    svc = PlanService(db)
    return await svc.get_all(only_active=only_active)


@router.get("/{plan_id}", response_model=PlanRead, summary="Get plan by ID")
async def get_plan(plan_id: int, db: AsyncSession = Depends(get_db)) -> PlanRead:
    svc = PlanService(db)
    plan = await svc.get_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
        )
    return plan


@router.post(
    "/",
    response_model=PlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create plan",
)
async def create_plan(
    data: PlanCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PlanRead:
    svc = PlanService(db)
    existing = await svc.get_by_slug(data.slug)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plan with slug '{data.slug}' already exists",
        )
    return await svc.create(**data.model_dump())


@router.patch("/{plan_id}", response_model=PlanRead, summary="Update plan")
async def update_plan(
    plan_id: int,
    data: PlanUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PlanRead:
    svc = PlanService(db)
    plan = await svc.update(plan_id, **data.model_dump(exclude_none=True))
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
        )
    return plan


@router.post(
    "/{plan_id}/toggle", response_model=PlanRead, summary="Toggle plan active state"
)
async def toggle_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PlanRead:
    svc = PlanService(db)
    plan = await svc.toggle_active(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
        )
    return plan


@router.delete(
    "/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete plan"
)
async def delete_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> None:
    svc = PlanService(db)
    deleted = await svc.delete(plan_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
        )
