from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..dependencies import get_list_or_404
from ..models import List
from .models import Metric
from .schemas import MetricCreate, MetricRead, MetricUpdate, MetricValues
from .services import MetricService


router = APIRouter(prefix="/{list_uuid}/metrics", tags=["metrics"])


async def _or_404(db: AsyncSession, lst: List, ref: str) -> Metric:
    """The metric this ref names, or 404.

    No visibility check of its own, unlike views and forms: a metric carries no
    permissions separate from its list, so reaching this route is the whole gate
    and there is nothing a guessed name could confirm that `list_metrics` does
    not already show.
    """
    metric = await MetricService.get_by_ref(db, lst.id, ref)
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Metric not found"
        )
    return metric


@router.post("/", response_model=MetricRead, status_code=status.HTTP_201_CREATED)
async def create_metric(
    payload: MetricCreate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    try:
        return await MetricService.create(db, lst, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[MetricRead])
async def list_metrics(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await MetricService.list(db, lst.id)


# Declared before `/{ref}`, or the static segment would be read as a metric name.
@router.get("/values", response_model=MetricValues)
async def metric_values(
    view_uuid: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every metric's value over the records this caller can see.

    Takes the same `view_uuid` and `q` as the items endpoint, so the numbers
    describe exactly what the grid is showing.
    """
    return await MetricService.evaluate(db, lst, current_user, view_uuid, q)


@router.get("/{ref}", response_model=MetricRead)
async def get_metric(
    ref: str,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await _or_404(db, lst, ref)


@router.patch("/{ref}", response_model=MetricRead)
async def update_metric(
    ref: str,
    payload: MetricUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    metric = await _or_404(db, lst, ref)
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    try:
        updated = await MetricService.update(db, lst, metric.uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Metric not found"
        )
    return updated


@router.delete("/{ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metric(
    ref: str,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    metric = await _or_404(db, lst, ref)
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    await MetricService.delete(db, lst.id, metric.uuid)
