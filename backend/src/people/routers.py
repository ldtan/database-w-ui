from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user, require_superuser
from src.auth.users.models import User
from src.auth.users.services import UserService
from src.core.exceptions import ConflictError
from src.infrastructure.database import get_db

from .models import Person
from .schemas import PersonCreate, PersonRead, PersonUpdate
from .services import PersonService

# A person record is personal data (names, birth date), so unlike /auth/users --
# whose reads stay open because the Share dialog needs to list principals -- these
# are readable only by the person themselves or a superuser.
router = APIRouter(
    prefix="/people",
    tags=["people"],
    dependencies=[Depends(get_current_user)],
)


def _require_self_or_superuser(current_user: User, person: Person) -> None:
    if person.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only access your own person record",
        )


@router.post("/", response_model=PersonRead, status_code=status.HTTP_201_CREATED)
async def create_person(
    payload: PersonCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates the caller's own profile. A superuser may create one for someone
    else by passing user_uuid."""
    if payload.user_uuid is None or payload.user_uuid == current_user.uuid:
        user_id = current_user.id
    else:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may only create your own person record",
            )
        target = await UserService.get_by_uuid(db, payload.user_uuid)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_uuid does not reference a user",
            )
        user_id = target.id

    try:
        return await PersonService.create(db, user_id, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/", response_model=list[PersonRead])
async def list_people(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match first, middle, or last name"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Superuser-only: this returns everyone's personal data."""
    return await PersonService.list(db, skip=skip, limit=limit, q=q)


# Declared before /{uuid} so "me" is not parsed as a uuid.
@router.get("/me", response_model=PersonRead)
async def get_my_person(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    person = await PersonService.get_by_user_id(db, current_user.id)
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You do not have a person record yet",
        )
    return person


@router.get("/{uuid}", response_model=PersonRead)
async def get_person(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    person = await PersonService.get_by_uuid(db, uuid)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    _require_self_or_superuser(current_user, person)
    return person


@router.patch("/{uuid}", response_model=PersonRead)
async def update_person(
    uuid: UUID,
    payload: PersonUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    person = await PersonService.get_by_uuid(db, uuid)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    _require_self_or_superuser(current_user, person)

    updated = await PersonService.update(db, uuid, payload)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    return updated


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    person = await PersonService.get_by_uuid(db, uuid)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    _require_self_or_superuser(current_user, person)
    await PersonService.delete(db, uuid)
