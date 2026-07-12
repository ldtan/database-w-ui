from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import UnknownReferenceError
from src.infrastructure.database import get_db

from .enums import Action, ResourceType
from .schemas import (
    EffectiveActions,
    EffectiveActionsQuery,
    GrantCreate,
    GrantRead,
    ResourceActions,
)
from .services import PermissionService

# PermissionDeniedError is turned into a 403 by an app-level exception handler.
router = APIRouter(
    prefix="/permissions",
    tags=["permissions"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/grants", response_model=GrantRead, status_code=status.HTTP_201_CREATED)
async def create_grant(
    payload: GrantCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await PermissionService.upsert_grant(db, current_user, payload)
    except UnknownReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/grants", response_model=list[GrantRead])
async def list_grants(
    resource_type: ResourceType = Query(...),
    resource_uuid: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    grants = await PermissionService.list_grants(
        db, current_user, resource_type, resource_uuid
    )
    if grants is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return grants


@router.delete("/grants/{grant_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_grant(
    grant_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not await PermissionService.revoke_grant(db, current_user, grant_uuid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found"
        )


@router.get("/effective", response_model=EffectiveActions)
async def effective_actions(
    resource_type: ResourceType = Query(...),
    resource_uuid: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    actions = await PermissionService.effective_actions_for_uuid(
        db, current_user, resource_type, resource_uuid
    )
    if actions is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return EffectiveActions(actions=_sorted(actions))


@router.post("/effective", response_model=list[ResourceActions])
async def effective_actions_batch(
    payload: EffectiveActionsQuery,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Effective actions for many resources at once, so the UI can gate per-row
    controls without one request per row. Unknown resources are omitted."""
    resolved = await PermissionService.effective_actions_bulk(
        db,
        current_user,
        [(ref.resource_type, ref.resource_uuid) for ref in payload.resources],
    )
    return [
        ResourceActions(
            resource_type=resource_type,
            resource_uuid=resource_uuid,
            actions=_sorted(actions),
        )
        for (resource_type, resource_uuid), actions in resolved.items()
    ]


def _sorted(actions: set[Action]) -> list[Action]:
    return sorted(actions, key=lambda action: action.value)
