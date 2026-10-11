"""Request ids, the body ceiling, and the handler that stops a 500 being silent.

None of this existed: two loggers in the whole tree, no configuration, no request
correlation, no catch-all. A production 500 was a traceback in an ephemeral log
stream on a single instance, with no id to match it to the report that arrived an
hour later.
"""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.core.config import settings
from src.infrastructure import database as db_module


@pytest_asyncio.fixture
async def tolerant_client(engine, session_factory, user_id):
    """A client that lets the app answer its own 500 instead of re-raising.

    `ASGITransport` defaults to `raise_app_exceptions=True`, which is right for the
    rest of the suite — an unexpected exception in a test should surface as that
    exception, not as an opaque 500. It is exactly wrong here, where the handler's
    *response* is the thing under test.

    `user_id` is depended on for the same reason the shared `client` fixture depends
    on it: DISABLE_AUTH resolves the caller to the first user, and with an empty
    users table every route answers 404 before reaching anything.
    """
    orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
    db_module.engine, db_module.SessionLocal = engine, session_factory
    app = create_app()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            headers={"Authorization": "Bearer test"},
        ) as c:
            yield c
    finally:
        db_module.engine, db_module.SessionLocal = orig_engine, orig_factory


async def test_a_request_id_comes_back_and_an_inbound_one_wins(client):
    resp = await client.get("/api/workspaces/")
    assert resp.status_code == 200
    generated = resp.headers.get("X-Request-ID")
    assert generated and len(generated) >= 8

    # A proxy that already assigned one keeps it, so a single request has a single
    # id across every hop that logged it.
    resp = await client.get("/api/workspaces/", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


async def test_an_unhandled_exception_is_a_500_that_says_nothing(
    tolerant_client, monkeypatch
):
    """The body must not carry the exception. An unhandled error's `str()` is
    exactly the internal detail the import path was fixed for leaking, and the
    default handler's behaviour depends on the ASGI server underneath."""
    from src.workspaces.services import WorkspaceService

    async def _boom(*args, **kwargs):
        raise RuntimeError("connection string postgres://user:hunter2@internal")

    monkeypatch.setattr(WorkspaceService, "count_visible", _boom)

    resp = await tolerant_client.get("/api/workspaces/")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal server error"
    assert "hunter2" not in resp.text
    assert "RuntimeError" not in resp.text
    # The id is what turns a user's report into the log line.
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_an_http_exception_still_answers_normally(client):
    """The catch-all must sit *under* the deliberate ones — a 404 is an answer, and
    turning it into a 500 would be the handler eating the app's own vocabulary."""
    resp = await client.get("/api/tables/00000000-0000-0000-0000-000000000000")
    assert resp.status_code in (403, 404), resp.text
    assert resp.json()["detail"] != "Internal server error"


async def test_a_body_over_the_ceiling_is_refused(client, table_uuid, add_column):
    """A JSON body had no limit at all: `TableRecordCreate.data` is a dict to Pydantic,
    and a `text` column's `max_length` is unset by default — so one request could
    buffer, parse, sanitise and store fifty megabytes on a 512 MB instance."""
    col = await add_column(table_uuid, name="notes", display_name="Notes", type="text")
    # Against MAX_REQUEST_BYTES, not MAX_UPLOAD_BYTES: the two answer different
    # questions and the backstop deliberately sits above the upload limit, so that
    # the import routes keep producing their own message naming their own limit.
    oversized = "A" * (settings.MAX_REQUEST_BYTES + 1024)

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: oversized}}
    )
    assert resp.status_code == 413, resp.status_code
    assert "too large" in resp.json()["detail"].lower()

    # And an ordinary body is untouched — the ceiling is a ceiling, not a squeeze.
    ok = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: "a normal note"}}
    )
    assert ok.status_code == 201, ok.text
