"""`GET /lists/{uuid}/events` — the stream an open list holds while it is on screen.

Three things about this route are unlike every other route in the app, and each one
is load-bearing rather than stylistic.

**1. It is its own router, mounted straight onto the app.** The natural home would be
`src/lists/routers.py`, and that router declares `dependencies=[Depends(get_current_user)]`
— which takes `Depends(get_db)`. A dependency with `yield` is torn down *after* the
response completes, and this response completes when the user closes the tab, so
every subscriber would hold a pooled session for the life of their connection. The
engine takes SQLAlchemy's defaults (5 + 10 overflow), so roughly fifteen people with a
list open would empty the pool and every other request in the app would block until
`pool_timeout`. Hence: no router-level dependencies, and the auth below is done by
hand inside a session that is closed before a single byte is streamed. This is the
"no pooled DB connection while open" rule from docs/known-gaps.md, and it is why
`tests/test_live_events.py` asserts that an ordinary request still answers while a
stream is open — nothing else would notice this breaking.

**2. Authorization happens once, at connect.** There is no session to re-check with, by
construction. `SSE_MAX_STREAM_SECONDS` is what bounds that: the stream ends, the client
reconnects, and the check runs again. So losing `view` on a list stops the signals at
the next reconnect rather than immediately — see the setting for why that is a bounded
cost and not a hole.

**3. It sends no data, ever.** An `event: records` frame means "this list changed, look
again". Subscribers of one list are entitled to different *records* of it
(`PermissionService.item_view_filter`), so anything on this channel would be visible to
all of them or to none, and the refetch it provokes is separately permissioned. The one
thing a subscriber does learn is *timing* — that a write happened somewhere in a list
they can view, including to a record they cannot see. That is documented in
docs/known-gaps.md and is the accepted cost of a shared channel.
"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from src.auth.dependencies import get_current_user, oauth2_scheme
from src.core.config import settings
from src.infrastructure import database as db_module
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..services import ListService
from .broker import broker, shutdown_event

# Mounted in main.py rather than included into the lists router — see the module
# docstring. The path is written out in full so it still reads as a list route.
router = APIRouter(prefix="/lists", tags=["events"])

# A comment frame. Legal SSE that every parser ignores, which is what makes it a
# heartbeat rather than an event.
_HEARTBEAT = b": ping\n\n"

# What a change looks like on the wire. Named `records` rather than `change` because
# it says what moved: extending this channel to schema changes later means a second
# event name, not a payload nobody can tell apart from this one.
_CHANGED = b"event: records\ndata: {}\n\n"


async def _authorize(token: str, list_uuid: UUID) -> int:
    """Resolve the caller and their `view` on the list. Returns the list's id.

    Every database access this route makes happens here, inside a session opened and
    closed within this function, so the caller can start streaming with nothing
    checked out of the pool. `get_current_user` and the body of `get_list_or_404` are
    called as plain functions rather than injected as dependencies for exactly that
    reason — teardown timing is the whole problem, and a direct call has none.

    The session factory is reached through the module (`db_module.SessionLocal`)
    rather than imported by name, and that is not a style choice: `from ... import
    SessionLocal` binds the factory at import time, so anything that swaps it
    afterwards — the test suite points it at the test database, exactly as
    `transfer.services.export_rows` and `backfill.run_deferred` both accommodate —
    would be ignored here and this route alone would keep talking to the real one.

    Only `lst.id` comes back. The ORM instance must not outlive the session: it is
    bound to a session that is about to close, and `expire_on_commit=False` does not
    make a detached instance safe to touch, it only makes the loaded attributes stay
    readable. An int cannot lazy-load anything from a stream generator that has no
    greenlet to do it in.
    """
    async with db_module.SessionLocal() as db:
        user = await get_current_user(token=token, db=db)
        lst = await ListService.get_any_by_uuid(db, list_uuid)
        if lst is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
            )
        await PermissionService.require(
            db, user, ResourceType.list, lst.id, Action.view
        )
        return lst.id


@router.get(
    "/{list_uuid}/events",
    summary="Subscribe to this list's record changes",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "An event stream. Emits `event: records` when this list's records "
                "change and `: ping` comments meanwhile. Carries no record data — "
                "re-read GET /items/ on each event. The server closes the stream "
                "periodically; reconnect."
            ),
        },
        503: {"description": "Live updates are disabled on this deployment."},
    },
)
async def list_events(
    list_uuid: UUID,
    token: str = Depends(oauth2_scheme),
):
    # Declared as a dependency because it reads the request and touches no database:
    # it answers 401 to a caller with no bearer token before anything here runs, and
    # it is what puts the security requirement on this route in the OpenAPI document
    # (so `npm run gen:api` still describes it).
    if not settings.SSE_ENABLED:
        # The client reads this as "fall back to polling", which is why it is a
        # refusal of the transport and not of the request.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live updates are disabled on this deployment.",
        )

    list_id = await _authorize(token, list_uuid)

    async def stream():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.SSE_MAX_STREAM_SECONDS
        stopping = shutdown_event()
        with broker.subscribe(list_id) as changed:
            # An immediate comment, before anything can have happened. It flushes
            # the response head through whatever proxies are in the way, so the
            # client learns the stream is open now rather than at the first write —
            # which on a quiet list could be never, and would be indistinguishable
            # from a connection that failed to establish.
            yield _HEARTBEAT
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                waiters = [
                    asyncio.ensure_future(changed.wait()),
                    asyncio.ensure_future(stopping.wait()),
                ]
                try:
                    await asyncio.wait(
                        waiters,
                        timeout=min(settings.SSE_HEARTBEAT_SECONDS, remaining),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    # Both are cancelled every time round: the loser of the race is
                    # a task still waiting on an event, and leaving it behind would
                    # accumulate one per heartbeat for the life of the connection.
                    for waiter in waiters:
                        waiter.cancel()

                if stopping.is_set():
                    return  # redeploy: end now rather than make shutdown wait
                if changed.is_set():
                    changed.clear()
                    yield _CHANGED
                else:
                    yield _HEARTBEAT  # the wait timed out; say something anyway
                # `await request.is_disconnected()` is deliberately not consulted.
                # Under a StreamingResponse the write above is what discovers a
                # closed peer, and asking separately adds a receive-channel read
                # that can swallow the disconnect message the server needs.

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            # `no-transform` matters as much as `no-cache`: a proxy that "helpfully"
            # compresses or rechunks an event stream is a proxy that buffers it, and
            # a buffered event stream delivers everything at once when it closes.
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx-family proxies buffer proxied responses by default. This is the
            # header that turns that off, and it is ignored harmlessly elsewhere.
            "X-Accel-Buffering": "no",
        },
    )
