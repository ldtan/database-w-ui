"""The `phone` column type, over the HTTP API.

The normalisation is the type. A regex can tell a phone number from `banana`, but
it cannot tell that `0917 123 4567`, `+63 917 123 4567` and `09171234567` are one
number — so `unique` would miss the duplicate it exists to catch, a filter for one
spelling would miss the others, and grouping would show one person twice. These
tests pin that every write path collapses them to one stored E.164 value.
"""
import pytest


async def _add_phone_column(client, list_uuid: str, name="mobile", **config) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "phone",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_a_number_with_its_country_code_needs_no_region(client, list_uuid):
    col = await _add_phone_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {col: "+63 917 123 4567"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "+639171234567"


@pytest.mark.parametrize(
    "typed",
    ["+63 917 123 4567", "+639171234567", "0917 123 4567", "09171234567", "  0917-123-4567  "],
)
async def test_every_spelling_stores_one_value(client, list_uuid, typed):
    col = await _add_phone_column(client, list_uuid, default_region="PH")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: typed}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "+639171234567"


async def test_unique_catches_the_same_number_typed_differently(client, list_uuid):
    """The whole point: without normalising, these are two values."""
    col = await _add_phone_column(client, list_uuid, default_region="PH", unique=True)
    first = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "0917 123 4567"}}
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "+63 917 123 4567"}}
    )
    assert second.status_code == 409, second.text


async def test_a_national_number_needs_a_region_to_be_read_at_all(client, list_uuid):
    """No server-side geography guess: a column that was never told which country
    a bare "0917..." belongs to refuses it rather than picking one."""
    col = await _add_phone_column(client, list_uuid)
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "0917 123 4567"}}
    )
    assert resp.status_code == 400, resp.text
    assert "country code" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "value",
    [
        "banana",
        "+63 900 000 0000",  # well-formed, dialable by nobody
        "12345",
        "",
        "+",
    ],
)
async def test_rejects_what_is_not_a_number(client, list_uuid, value):
    col = await _add_phone_column(client, list_uuid, default_region="PH")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
    )
    # An empty string is an empty cell, not a bad value — it is nulled before the
    # check ever runs, like every other type.
    expected = 201 if value == "" else 400
    assert resp.status_code == expected, f"{value!r}: {resp.text}"


async def test_allowed_regions_narrow_what_the_column_takes(client, list_uuid):
    col = await _add_phone_column(
        client, list_uuid, default_region="PH", allowed_regions=["PH"]
    )
    ok = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "+639171234567"}}
    )
    assert ok.status_code == 201, ok.text

    # A perfectly valid US number, refused because this column is not for them.
    nope = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "+12125551234"}}
    )
    assert nope.status_code == 400, nope.text


@pytest.mark.parametrize(
    "config",
    [
        {"default_region": "PHL"},  # alpha-3, not alpha-2
        {"default_region": "ZZ"},
        {"allowed_regions": ["PH", "NOWHERE"]},
        {"display_format": "shouty"},
        # The region that makes a national number parseable is the one the check
        # would then reject it for: a column nobody can fill by typing.
        {"default_region": "PH", "allowed_regions": ["US"]},
    ],
)
async def test_a_broken_config_is_refused_at_config_time(client, list_uuid, config):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "mobile",
            "display_name": "Mobile",
            "type": "phone",
            "config": config,
        },
    )
    assert resp.status_code == 400, resp.text


async def test_a_columns_default_is_validated_and_normalized(client, list_uuid):
    bad = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "mobile",
            "display_name": "Mobile",
            "type": "phone",
            "config": {"default_region": "PH", "default": "banana"},
        },
    )
    assert bad.status_code == 400, bad.text

    good = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "mobile",
            "display_name": "Mobile",
            "type": "phone",
            # Normalised against the region set in the *same* request.
            "config": {"default_region": "PH", "default": "0917 123 4567"},
        },
    )
    assert good.status_code == 201, good.text
    assert good.json()["config"]["default"] == "+639171234567"


async def test_import_normalizes_and_rejects_row_by_row(client, list_uuid):
    col = await _add_phone_column(client, list_uuid, default_region="PH")
    csv = "mobile\nbanana\n0917 123 4567\n"
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 1

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [r["data"][col] for r in items] == ["+639171234567"]


async def test_export_writes_e164_and_reimports_unchanged(client, list_uuid):
    col = await _add_phone_column(client, list_uuid, default_region="PH")
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "0917 123 4567"}}
    )

    exported = await client.post(
        f"/api/lists/{list_uuid}/export", json={"format": "csv"}
    )
    assert exported.status_code == 200, exported.text
    assert "+639171234567" in exported.text

    again = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", exported.text, "text/csv")},
    )
    assert again.status_code == 200, again.text
    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [r["data"][col] for r in items] == ["+639171234567", "+639171234567"]


async def test_converting_a_text_column_cleans_it_up(client, list_uuid, add_column):
    """The motivating conversion: a free-text phone column, tidied into one form,
    with the rows that cannot be read named in the report rather than dropped."""
    text = await add_column(
        list_uuid, name="mobile", display_name="Mobile", type="text"
    )
    for typed in ("0917 123 4567", "+63 917 123 4567", "not a number"):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {text: typed}}
        )
        assert resp.status_code == 201, resp.text

    report = await client.post(
        f"/api/lists/{list_uuid}/columns/{text}/convert",
        json={"type": "phone", "config": {"default_region": "PH"}},
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["new_type"] == "phone"
    assert body["converted"] == 2
    assert body["failed"] == 1
    assert len(body["errors"]) == 1

    applied = await client.post(
        f"/api/lists/{list_uuid}/columns/{text}/convert/apply",
        json={
            "type": "phone",
            "config": body["config"],
            # A conversion is a point of no return, so it refuses unless the caller
            # has seen the report — which is what makes the failing row a decision
            # rather than a silent loss.
        },
    )
    assert applied.status_code == 200, applied.text

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    # Both readable numbers arrive in one form; the third cell is left empty, and
    # its original is still in the binned `mobile__before_conversion` column.
    assert sorted(
        v for v in (r["data"].get(text) for r in items) if v is not None
    ) == ["+639171234567", "+639171234567"]
