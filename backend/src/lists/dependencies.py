from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from .models import List
from .services import ListService
from .views.schemas import FilterGroup

_FILTERS_DESCRIPTION = (
    "Filter the records for this request only, as a URL-encoded JSON filter group: "
    '{"conjunction": "and"|"or", "conditions": [{"column_uuid", "operator", "value"}]}. '
    "Replaces the view's saved filters; an empty `conditions` list means no filter at "
    "all, which is how a caller asks for the whole list through a filtered view."
)


async def resolve_list_or_404(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List:
    """Resolve a live list by uuid without a permission check.

    Item routes depend on this because access to an item can be granted directly,
    even when the caller cannot view the list as a whole; each item endpoint then
    enforces the appropriate per-item permission.
    """
    lst = await ListService.get_any_by_uuid(db, list_uuid)
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    return lst


async def get_list_or_404(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List:
    """Resolve a list the current user can view, or raise 404/403.

    List-level routers (columns, forms, views, transfer, list reads) depend on this
    so 'view' access is enforced in one place.
    """
    lst = await ListService.get_any_by_uuid(db, list_uuid)
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.view
    )
    return lst


def filter_param(
    filters: Optional[str] = Query(None, description=_FILTERS_DESCRIPTION),
) -> Optional[dict]:
    """A per-request filter, parsed from the query string.

    Filtering used to be reachable only by saving the conditions onto a view, which
    made a temporary question ("just the overdue ones, for a minute") a permanent,
    shared change needing `configure` on that view. This is the same structure the
    view stores, sent per request instead — so the grid, the record cards and the
    metric strip can all ask a narrower question without anyone editing anything.

    Parsed by hand rather than declared as `Json[FilterGroup]`, which FastAPI
    rejects in a query position. Returns None when absent, which the services read
    as "no override — use whatever the view saved"; that is deliberately different
    from a group with no conditions, which means "no filter".

    A 422 for a malformed group, like any other bad parameter. Nothing here decides
    what a caller may *see*: the permission filter is applied separately and always,
    and `apply_filters` ignores conditions naming an unknown column or operator.
    """
    if filters is None:
        return None
    try:
        return FilterGroup.model_validate_json(filters).model_dump(mode="json")
    except PydanticValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid 'filters' parameter: {exc.errors()[0].get('msg', 'malformed')}",
        ) from exc
