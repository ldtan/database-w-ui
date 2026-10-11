"""The `/api/lists/*` -> `/api/tables/*` compatibility redirect.

**Delete this file with `src/tables/legacy.py` after 2026-11-11.**

What is worth pinning is not that a redirect happens but that it is *usable*: a 308
keeps the method and body, the nested `items` segment moves to `records` too, and the
query string survives. A 302 here would silently turn every legacy POST into a GET,
which is the kind of thing that looks fine in a smoke test and loses a write.
"""
import pytest


async def test_a_legacy_collection_url_redirects(client):
    resp = await client.get("/api/lists/", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/api/tables/"


async def test_a_legacy_record_url_rewrites_items_to_records(client, table_uuid):
    resp = await client.get(
        f"/api/lists/{table_uuid}/items/", follow_redirects=False
    )
    assert resp.status_code == 308
    assert resp.headers["location"] == f"/api/tables/{table_uuid}/records/"


async def test_the_query_string_survives(client, table_uuid):
    resp = await client.get(
        f"/api/lists/{table_uuid}/items/?limit=5&skip=10", follow_redirects=False
    )
    assert resp.status_code == 308
    assert (
        resp.headers["location"]
        == f"/api/tables/{table_uuid}/records/?limit=5&skip=10"
    )


@pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
async def test_writes_redirect_too(client, method):
    """308 is the only redirect that keeps a write a write."""
    resp = await getattr(client, method)(
        "/api/lists/some-uuid", follow_redirects=False
    )
    assert resp.status_code == 308


async def test_following_the_redirect_reaches_the_real_route(client, table_uuid):
    """End to end: the point of the shim is that an old client still works."""
    resp = await client.get(f"/api/lists/{table_uuid}/items/", follow_redirects=True)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_the_legacy_routes_stay_out_of_the_openapi_document():
    """`npm run gen:api` reads this document, so the old spelling must not appear in
    it — otherwise the generated client learns the dead routes and something new
    starts depending on them."""
    from main import create_app

    paths = create_app().openapi()["paths"]
    assert not [p for p in paths if "/lists" in p]
