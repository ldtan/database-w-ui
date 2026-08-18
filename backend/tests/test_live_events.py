"""Live records: the broker, and the stream `GET /lists/{uuid}/events` serves.

Two things about testing this are unlike the rest of the suite, and both shape
everything below.

**httpx's `ASGITransport` buffers the whole response.** It runs the app to completion
(`assert response_complete.is_set()`) and only then hands back a `Response`, so there
is no way to read frames as they are produced — and an unbounded stream would simply
hang the suite forever. Every test here therefore drives the stream to its natural end
by setting `SSE_MAX_STREAM_SECONDS` to a fraction of a second, and reads the frames out
of the buffered body afterwards. Where a test needs something to *happen* while the
stream is open, it starts the request as a task, does the thing, and then awaits it.

**The route resolves its caller by calling `get_current_user` directly**, not by
declaring it as a dependency — that is the whole point of it (see the module docstring
in src/lists/events/routers.py). So `app.dependency_overrides` does not reach it, and
`make_client` from test_permissions cannot be borrowed. The permission tests here mint
real tokens with `DISABLE_AUTH` off instead, which is the better test anyway: the
hand-rolled auth is the part with no framework underneath it.
"""
import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.helpers import create_access_token
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.core.config import settings
from src.infrastructure import database as db_module
from src.lists.events.broker import ListEventBroker, broker, reset_shutdown
from src.lists.schemas import ListCreate
from src.lists.services import ListService
from src.permissions.enums import Action, ResourceType
from src.permissions.schemas import GrantCreate
from src.permissions.services import PermissionService
from src.workspaces.schemas import WorkspaceCreate
from src.workspaces.services import WorkspaceService

CHANGED = "event: records"
PING = ": ping"


def _count(body: str, marker: str) -> int:
    return sum(1 for line in body.splitlines() if line.strip() == marker.strip())


@pytest.fixture(autouse=True)
def _short_streams():
    """Make every stream in this module end on its own, quickly.

    The heartbeat is left comfortably longer than the lifetime by default so a test
    that does not ask for pings gets exactly one (the opening flush) and no timing
    noise; the heartbeat test lowers it itself.
    """
    original = (
        settings.SSE_MAX_STREAM_SECONDS,
        settings.SSE_HEARTBEAT_SECONDS,
        settings.SSE_ENABLED,
    )
    settings.SSE_MAX_STREAM_SECONDS = 1
    settings.SSE_HEARTBEAT_SECONDS = 10
    settings.SSE_ENABLED = True
    reset_shutdown()
    yield
    (
        settings.SSE_MAX_STREAM_SECONDS,
        settings.SSE_HEARTBEAT_SECONDS,
        settings.SSE_ENABLED,
    ) = original
    reset_shutdown()


# -- the broker ------------------------------------------------------------


async def test_notify_wakes_every_watcher_of_that_list_and_no_others():
    bus = ListEventBroker()
    with bus.subscribe(1) as first, bus.subscribe(1) as second, bus.subscribe(2) as other:
        bus.notify(1)
        assert first.is_set() and second.is_set()
        assert not other.is_set()


async def test_a_burst_of_notifies_coalesces_into_one_wake_up():
    """The property the Event mailbox exists for, and the reason it is not a queue.

    An event carries no content, so "200 rows were imported" and "something changed"
    are the same message. A queue would deliver 200 of them and the client would
    refetch 200 times; one bit of state cannot.
    """
    bus = ListEventBroker()
    with bus.subscribe(1) as changed:
        for _ in range(200):
            bus.notify(1)
        assert changed.is_set()
        changed.clear()
        # Nothing was banked up behind the first one.
        assert not changed.is_set()


async def test_notifying_a_list_nobody_watches_is_a_no_op():
    bus = ListEventBroker()
    bus.notify(999)  # must not raise, and must not create a watcher set
    assert bus.watcher_count(999) == 0


async def test_a_subscriber_is_removed_however_its_block_ends():
    bus = ListEventBroker()
    with bus.subscribe(1):
        assert bus.watcher_count(1) == 1
    assert bus.watcher_count(1) == 0

    with pytest.raises(RuntimeError):
        with bus.subscribe(1):
            assert bus.watcher_count(1) == 1
            raise RuntimeError("boom")
    # A stream that dies mid-flight must not be left in the set being woken forever.
    assert bus.watcher_count(1) == 0


# -- the stream ------------------------------------------------------------


@pytest_asyncio.fixture
async def live_list(client, list_uuid):
    """A list with one text column.

    Returns `(list uuid, column uuid, list id)`. The column's **uuid** is the key a
    record's `data` is written under (data-model D2), and the list's integer id is
    what the broker is keyed on.
    """
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "title", "display_name": "Title", "type": "text"},
    )
    assert resp.status_code == 201, resp.text
    column_uuid = resp.json()["uuid"]
    async with db_module.SessionLocal() as db:
        lst = await ListService.get_any_by_uuid(db, list_uuid)
        return list_uuid, column_uuid, lst.id


async def test_a_stream_opens_pings_and_ends_at_its_deadline(client, live_list):
    list_uuid, column_uuid, _ = live_list
    resp = await client.get(f"/api/lists/{list_uuid}/events")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # The anti-buffering headers are the difference between "live" and "everything
    # at once when the tab closes", so they are asserted rather than assumed.
    assert resp.headers["cache-control"] == "no-cache, no-transform"
    assert resp.headers["x-accel-buffering"] == "no"
    # The opening comment: it flushes the response head so the client knows the
    # stream is open before anything has happened on a list that may be quiet.
    assert resp.text.startswith(PING)
    # And it ended by itself, which is what SSE_MAX_STREAM_SECONDS is for.
    assert _count(resp.text, CHANGED) == 0


async def test_an_idle_stream_keeps_saying_something(client, live_list):
    """Without traffic a proxy reaps the connection and the server never learns the
    peer has gone. More than one ping in a second of silence is the whole assertion."""
    settings.SSE_HEARTBEAT_SECONDS = 0.2
    list_uuid, column_uuid, _ = live_list
    resp = await client.get(f"/api/lists/{list_uuid}/events")
    assert _count(resp.text, PING) >= 3


async def test_a_record_write_reaches_an_open_stream(client, live_list):
    """The feature, end to end: POST a record and the watcher is told to look again."""
    list_uuid, column_uuid, _ = live_list
    stream = asyncio.create_task(client.get(f"/api/lists/{list_uuid}/events"))
    await asyncio.sleep(0.1)  # let the subscription register before writing

    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {column_uuid: "hello"}}
    )
    assert created.status_code == 201, created.text

    resp = await stream
    assert _count(resp.text, CHANGED) == 1
    # And it says nothing about the record. A subscriber's per-record permissions
    # differ from every other subscriber's, so the channel carries no data at all.
    assert "hello" not in resp.text


async def test_the_frame_carries_no_record_data_for_any_write(client, live_list):
    """The same guarantee across the other write paths, since each one is its own
    `notify` call site and a payload could be introduced at any of them."""
    list_uuid, column_uuid, _ = live_list
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {column_uuid: "secret"}}
    )
    item_uuid = created.json()["uuid"]

    stream = asyncio.create_task(client.get(f"/api/lists/{list_uuid}/events"))
    await asyncio.sleep(0.1)
    patched = await client.patch(
        f"/api/lists/{list_uuid}/items/{item_uuid}", json={"data": {column_uuid: "other"}}
    )
    assert patched.status_code == 200, patched.text

    resp = await stream
    assert _count(resp.text, CHANGED) == 1
    assert "secret" not in resp.text and "other" not in resp.text
    assert item_uuid not in resp.text


async def test_writes_to_another_list_do_not_wake_this_stream(
    client, live_list, workspace_uuid
):
    list_uuid, column_uuid, _ = live_list
    other = await client.post(
        "/api/lists/",
        json={"name": "l2", "display_name": "L2", "workspace_uuid": workspace_uuid},
    )
    other_uuid = other.json()["uuid"]
    await client.post(
        f"/api/lists/{other_uuid}/columns/",
        json={"name": "title", "display_name": "Title", "type": "text"},
    )

    stream = asyncio.create_task(client.get(f"/api/lists/{list_uuid}/events"))
    await asyncio.sleep(0.1)
    await client.post(
        f"/api/lists/{other_uuid}/items/", json={"data": {column_uuid: "elsewhere"}}
    )

    resp = await stream
    assert _count(resp.text, CHANGED) == 0


async def test_an_import_wakes_watchers_once_not_once_per_row(client, live_list):
    """A 200-row import must cost one refetch, not 200. Half of that is the import
    notifying once; the other half is the Event mailbox, which would coalesce it
    anyway. Both are worth having — the granularity is deliberate, not incidental."""
    list_uuid, column_uuid, _ = live_list
    csv = "title\n" + "\n".join(f"row {n}" for n in range(200))

    stream = asyncio.create_task(client.get(f"/api/lists/{list_uuid}/events"))
    await asyncio.sleep(0.1)
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 200

    events = await stream
    assert _count(events.text, CHANGED) == 1


async def test_a_bulk_delete_that_moves_nothing_says_nothing(client, live_list):
    """A watcher woken for a write that did not happen refetches an unchanged page,
    which is the cost polling had and push exists to remove."""
    list_uuid, column_uuid, _ = live_list
    stream = asyncio.create_task(client.get(f"/api/lists/{list_uuid}/events"))
    await asyncio.sleep(0.1)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete",
        json={"uuids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 0

    events = await stream
    assert _count(events.text, CHANGED) == 0


async def test_shutdown_ends_an_open_stream(client, live_list):
    """Otherwise a stream is an in-flight request forever and uvicorn's graceful
    shutdown waits out every subscriber's full lifetime on each redeploy."""
    from src.lists.events.broker import begin_shutdown

    settings.SSE_MAX_STREAM_SECONDS = 30  # long enough that only shutdown can end it
    settings.SSE_HEARTBEAT_SECONDS = 0.1
    list_uuid, column_uuid, _ = live_list

    stream = asyncio.create_task(client.get(f"/api/lists/{list_uuid}/events"))
    await asyncio.sleep(0.2)
    begin_shutdown()

    resp = await asyncio.wait_for(stream, timeout=5)
    assert resp.status_code == 200


async def test_the_lifespan_flips_the_shutdown_flag(engine, session_factory):
    """`ASGITransport` never runs lifespan events, so every other test in this file
    exercises `begin_shutdown` without ever proving that anything *calls* it.

    That gap is worth closing rather than living with: if the wiring in `main.py`
    were dropped, the whole suite would still be green and the symptom would be a
    Render deploy that takes SSE_MAX_STREAM_SECONDS to roll over, because uvicorn's
    graceful shutdown waits for every open stream. Entering and leaving the real
    context manager is the smallest thing that would notice.
    """
    from main import create_app, lifespan
    from src.lists.events.broker import shutdown_event

    orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
    db_module.engine, db_module.SessionLocal = engine, session_factory
    try:
        app = create_app()
        async with lifespan(app):
            # Cleared on the way in, so an app built twice in one process — which is
            # exactly what this suite does — does not start out already shut down.
            assert not shutdown_event().is_set()
        assert shutdown_event().is_set()
    finally:
        db_module.engine, db_module.SessionLocal = orig_engine, orig_factory


async def test_an_unknown_list_is_a_404(client):
    resp = await client.get(
        "/api/lists/00000000-0000-0000-0000-000000000000/events"
    )
    assert resp.status_code == 404


async def test_the_route_can_be_switched_off(client, live_list):
    """`SSE_ENABLED=False` is the deployment's kill switch. It answers 503 rather
    than 404 because the client reads it as "fall back to polling", which is a
    statement about the transport and not about the list."""
    settings.SSE_ENABLED = False
    list_uuid, column_uuid, _ = live_list
    resp = await client.get(f"/api/lists/{list_uuid}/events")
    assert resp.status_code == 503


async def test_a_caller_with_no_token_is_refused(client, live_list):
    list_uuid, column_uuid, _ = live_list
    resp = await client.get(
        f"/api/lists/{list_uuid}/events", headers={"Authorization": ""}
    )
    assert resp.status_code == 401


# -- the part with no framework underneath it ------------------------------


@pytest_asyncio.fixture
async def real_auth():
    """Turn `DISABLE_AUTH` off for one test, so tokens are actually read.

    The rest of the suite runs with it on and resolves every caller to the seeded
    user, which cannot express "somebody else". This route resolves its caller by
    hand — `dependency_overrides` does not reach a direct call — so the only way to
    test the gate is to authenticate for real.
    """
    settings.DISABLE_AUTH = False
    try:
        yield
    finally:
        settings.DISABLE_AUTH = True


def _token(user) -> str:
    return create_access_token({"sub": str(user.uuid), "username": user.username})


@pytest_asyncio.fixture
def app_client(engine, session_factory):
    """A client with no ambient Authorization header, so each call brings its own."""

    def _build():
        db_module.engine = engine
        db_module.SessionLocal = session_factory
        return AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://test"
        )

    return _build


async def test_the_stream_is_gated_on_being_able_to_view_the_list(
    session_factory, app_client, real_auth
):
    async with session_factory() as db:
        owner = await UserService.create(
            db, UserCreate(username="owner", email="owner@example.com", password="pw")
        )
        stranger = await UserService.create(
            db, UserCreate(username="nosy", email="nosy@example.com", password="pw")
        )
        ws = await WorkspaceService.create(
            db, owner.id, WorkspaceCreate(name="w", display_name="W")
        )
        lst = await ListService.create(
            db, ListCreate(name="l", display_name="L", workspace_uuid=ws.uuid)
        )
        list_uuid, list_id = str(lst.uuid), lst.id
        owner_token, stranger_token = _token(owner), _token(stranger)
        stranger_uuid = str(stranger.uuid)

    async with app_client() as http:
        url = f"/api/lists/{list_uuid}/events"

        # The owner is let in.
        resp = await http.get(url, headers={"Authorization": f"Bearer {owner_token}"})
        assert resp.status_code == 200

        # Somebody with no grant is not — and, the point of the test, is therefore
        # never woken by a write they would learn about only from the timing.
        resp = await http.get(
            url, headers={"Authorization": f"Bearer {stranger_token}"}
        )
        assert resp.status_code == 403
        assert broker.watcher_count(list_id) == 0

    # Given `view`, the same person is let in. This is also the seam that closes
    # late: authorization is checked at connect and never again, so revoking this
    # grant stops the signals at the subscriber's next reconnect — bounded by
    # SSE_MAX_STREAM_SECONDS — rather than immediately.
    async with session_factory() as db:
        granter = await UserService.get_by_username(db, "owner")
        await PermissionService.upsert_grant(
            db,
            granter,
            GrantCreate(
                resource_type=ResourceType.list,
                resource_uuid=list_uuid,
                user_uuid=stranger_uuid,
                actions=[Action.view],
            ),
        )
    async with app_client() as http:
        resp = await http.get(
            f"/api/lists/{list_uuid}/events",
            headers={"Authorization": f"Bearer {stranger_token}"},
        )
        assert resp.status_code == 200


async def test_an_open_stream_does_not_hold_a_pooled_connection(client, live_list):
    """The regression test for the reason this route is not on the lists router.

    `src/lists/routers.py` applies `Depends(get_current_user)`, which takes
    `Depends(get_db)`, and a yield dependency is released only once the response
    completes — which for a stream is when the browser closes it. Mounting the route
    there would check a session out of the pool per subscriber, and the engine takes
    SQLAlchemy's defaults (5 + 10 overflow), so roughly fifteen open lists would
    stall every other request in the app until `pool_timeout`.

    Nothing else in the suite would notice that: the app would still build, the route
    would still stream, and the failure would arrive in production as "the site hangs
    when a few people have a list open". So: more streams than the pool can hold,
    open at once, and an ordinary request answered while they are.
    """
    settings.SSE_MAX_STREAM_SECONDS = 3
    settings.SSE_HEARTBEAT_SECONDS = 0.2
    list_uuid, column_uuid, _ = live_list

    streams = [
        asyncio.create_task(client.get(f"/api/lists/{list_uuid}/events"))
        for _ in range(20)
    ]
    try:
        await asyncio.sleep(0.3)
        resp = await asyncio.wait_for(
            client.get(f"/api/lists/{list_uuid}/items/"), timeout=10
        )
        assert resp.status_code == 200
    finally:
        for task in streams:
            task.cancel()
        await asyncio.gather(*streams, return_exceptions=True)
