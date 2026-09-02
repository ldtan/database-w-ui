from fastapi import APIRouter, Depends, Query

from src.auth.dependencies import get_current_user, oauth2_scheme
from src.infrastructure import database as db_module

from .schemas import GeocodeResponse
from .services import search

router = APIRouter(prefix="/geocode", tags=["geocode"])


async def _authenticate(token: str) -> None:
    """Resolve the caller inside a session opened and closed before the search runs.

    Authenticated is the whole access rule: geocoding is not about any particular
    list, so there is no resource to check a permission against. What the check buys
    is that this cannot be used as an open relay to a third party under the
    deployment's own identity — the thing that would get its User-Agent blocked and
    take every location column down with it.

    **Why this is a hand-rolled call and not `dependencies=[Depends(get_current_user)]`,
    which is what it used to be.** That dependency takes `Depends(get_db)`, and a
    `yield` dependency is released only when the response completes — so the session
    was held for the whole of `search`, which queues every caller on a one-permit
    semaphore to honour Nominatim's one-request-per-second policy and then waits up to
    `GEOCODE_TIMEOUT_SECONDS` on the network. A pooled connection per *queued* geocode
    is the same shape of bug the events route documents at length: the engine takes
    SQLAlchemy's defaults (5 + 10 overflow), so a location picker debouncing a typed
    address from a handful of tabs could empty the pool and stall every unrelated
    request in the app until `pool_timeout`. The rule is the one in
    docs/known-gaps.md — a request must never hold a pooled connection across a wait
    it does not control.

    `db_module.SessionLocal` is reached through the module rather than imported by
    name so the test suite's swap is honoured, for the reason `events/routers.py`
    spells out. Nothing comes back: `get_current_user` either resolves or raises 401,
    and the resolved user is of no further use here.
    """
    async with db_module.SessionLocal() as db:
        await get_current_user(token=token, db=db)


@router.get("/", response_model=GeocodeResponse)
async def geocode(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(5, ge=1, le=10),
    # Declared as a dependency because it reads the request and touches no database:
    # it answers 401 to a caller with no bearer token before anything here runs, and
    # it is what puts the security requirement on this route in the OpenAPI document
    # (so `npm run gen:api` still describes it). Same arrangement as the events route.
    token: str = Depends(oauth2_scheme),
) -> GeocodeResponse:
    """Candidates for a typed address.

    Never fails: an unconfigured or unreachable geocoder answers `available: false`
    with a sentence to show, because a location column is usable without one and a
    502 here would make somebody's address field break for an outage elsewhere.
    """
    await _authenticate(token)
    return await search(q, limit)
