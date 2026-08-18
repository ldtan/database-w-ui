from fastapi import APIRouter, Depends, Query

from src.auth.dependencies import get_current_user

from .schemas import GeocodeResponse
from .services import search

# Authenticated, and that is the whole access rule: geocoding is not about any
# particular list, so there is no resource to check a permission against. What the
# dependency buys is that this cannot be used as an open relay to a third party
# under the deployment's own identity — the thing that would get its User-Agent
# blocked and take every location column down with it.
router = APIRouter(
    prefix="/geocode",
    tags=["geocode"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/", response_model=GeocodeResponse)
async def geocode(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(5, ge=1, le=10),
) -> GeocodeResponse:
    """Candidates for a typed address.

    Never fails: an unconfigured or unreachable geocoder answers `available: false`
    with a sentence to show, because a location column is usable without one and a
    502 here would make somebody's address field break for an outage elsewhere.
    """
    return await search(q, limit)
