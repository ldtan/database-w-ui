"""The `link` column type, over the HTTP API.

The scheme check is a security boundary, not a formatting preference: a link
cell is rendered as an anchor, so a `javascript:` or `data:` value stored here
would be stored XSS. These tests pin that it cannot be stored — via a record,
via a column default, or by widening `allowed_schemes`.
"""
import pytest


async def _add_link_column(client, list_uuid: str, **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "site",
            "display_name": "Site",
            "type": "link",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_accepts_an_http_url(client, list_uuid):
    col = await _add_link_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: "https://example.com/docs?a=1"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "https://example.com/docs?a=1"


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",  # urlsplit lowercases the scheme
        "java\nscript:alert(1)",  # ASCII whitespace is stripped, not a bypass
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
        "example.com",  # no scheme at all
        "https://",  # scheme but no host
    ],
)
async def test_rejects_unsafe_or_malformed_urls(client, list_uuid, value):
    col = await _add_link_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
    )
    assert resp.status_code == 400, f"{value!r} was accepted: {resp.text}"


async def test_allowed_schemes_narrows_but_cannot_widen(client, list_uuid):
    # A column may opt into a narrower safe set...
    col = await _add_link_column(client, list_uuid, allowed_schemes=["mailto"])
    ok = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: "mailto:someone@example.com"}},
    )
    assert ok.status_code == 201, ok.text
    # ...and http is then no longer accepted on that column.
    nope = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "https://example.com"}}
    )
    assert nope.status_code == 400

    # But it cannot re-enable a dangerous scheme: whoever configures a column is
    # an ordinary list editor, so this must be rejected at column-create time.
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "evil",
            "display_name": "Evil",
            "type": "link",
            "config": {"allowed_schemes": ["javascript"]},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_a_columns_default_is_validated_too(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "site",
            "display_name": "Site",
            "type": "link",
            "config": {"default": "javascript:alert(1)"},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_import_cannot_smuggle_an_unsafe_url(client, list_uuid):
    """Import goes through the same validation, so a CSV is not a side door."""
    col = await _add_link_column(client, list_uuid)
    csv = "site\njavascript:alert(1)\nhttps://example.com\n"
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 1

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [i["data"][col] for i in items] == ["https://example.com"]
