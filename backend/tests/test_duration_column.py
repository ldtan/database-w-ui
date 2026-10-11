"""The `duration` column type, over the HTTP API.

A cell holds **seconds**, and the export writes those seconds plainly — so the
property under test throughout is that a bare number always means seconds, whatever
the column is formatted as. That is what makes an exported file import back
unchanged; the written forms ("1:30", "90m") sit on top of it for whoever is typing
a file by hand.
"""
import json

import pytest


async def _add_duration_column(client, table_uuid: str, name="length", **config) -> str:
    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "duration",
            "config": config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def test_a_cell_holds_seconds(client, table_uuid):
    col = await _add_duration_column(client, table_uuid)
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: 5400}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == 5400


@pytest.mark.parametrize("value", [-1, "5400", True])
async def test_rejects_what_is_not_a_length_of_time(client, table_uuid, value):
    """A table is deliberately absent from this table: `coerce_shape` takes the
    first element on a column that does not declare `multiple`, so `[60]` reads
    as 60 — the same tolerance every other type gets."""
    col = await _add_duration_column(client, table_uuid)
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: value}}
    )
    assert resp.status_code == 400, f"{value!r} was accepted: {resp.text}"


async def test_min_and_max_bound_it_in_seconds(client, table_uuid):
    col = await _add_duration_column(client, table_uuid, min=60, max=3600)
    ok = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: 1800}}
    )
    assert ok.status_code == 201, ok.text
    for out_of_range in (30, 7200):
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {col: out_of_range}}
        )
        assert resp.status_code == 400, resp.text


async def test_a_bad_format_is_refused_at_config_time(client, table_uuid):
    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": "length",
            "display_name": "Length",
            "type": "duration",
            "config": {"duration_format": "fortnights"},
        },
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize(
    "written,seconds",
    [
        ("5400", 5400),  # a bare number is always seconds
        ("1:30", 5400),  # colon form: h:mm by default
        ("1:30:00", 5400),  # three parts are always h:mm:ss
        ("0:45:30", 2730),
        ("90m", 5400),
        ("1.5h", 5400),
        ("1h30m", 5400),
        ("45s", 45),
    ],
)
async def test_import_reads_the_written_forms(client, table_uuid, written, seconds):
    col = await _add_duration_column(client, table_uuid)
    resp = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("rows.csv", f"length\n{written}\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1, resp.text

    records = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert records[0]["data"][col] == seconds


async def test_the_colon_form_follows_the_columns_format(client, table_uuid):
    """"1:30" is an hour and a half on an h:mm column and ninety seconds on an
    m:ss one — because that is what each column has been showing."""
    col = await _add_duration_column(client, table_uuid, duration_format="m:ss")
    resp = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("rows.csv", "length\n1:30\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    records = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert records[0]["data"][col] == 90


@pytest.mark.parametrize("written", ["1:75", "an hour", "1:2:3:4", "90x"])
async def test_import_refuses_what_it_cannot_read(client, table_uuid, written):
    """"1:75" is a typo, not a silent 2:15."""
    await _add_duration_column(client, table_uuid)
    resp = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("rows.csv", f"length\n{written}\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 0, body
    assert len(body["errors"]) == 1


async def test_export_writes_seconds_and_reimports_unchanged(client, table_uuid):
    col = await _add_duration_column(client, table_uuid, duration_format="h:mm")
    await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: 5400}})

    exported = await client.post(
        f"/api/tables/{table_uuid}/export", json={"format": "csv"}
    )
    assert exported.status_code == 200, exported.text
    assert "5400" in exported.text

    again = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("rows.csv", exported.text, "text/csv")},
    )
    assert again.status_code == 200, again.text
    records = (await client.get(f"/api/tables/{table_uuid}/records/")).json()
    assert [r["data"][col] for r in records] == [5400, 5400]


async def test_it_sorts_and_filters_as_a_number(client, table_uuid):
    col = await _add_duration_column(client, table_uuid)
    for value in (600, 5400, 90):
        resp = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {col: value}}
        )
        assert resp.status_code == 201, resp.text

    ordered = await client.get(
        f"/api/tables/{table_uuid}/records/",
        params={"sort_by": col, "sort_dir": "asc"},
    )
    # As text these order 5400, 600, 90 — the leading digit decides.
    assert [r["data"][col] for r in ordered.json()] == [90, 600, 5400]

    filters = json.dumps(
        {
            "conjunction": "and",
            "conditions": [{"column_uuid": col, "operator": "gte", "value": 600}],
        }
    )
    long_ones = await client.get(
        f"/api/tables/{table_uuid}/records/", params={"filters": filters}
    )
    assert sorted(r["data"][col] for r in long_ones.json()) == [600, 5400]


async def test_a_metric_can_total_it(client, table_uuid):
    """"Total hours" is the motivating metric, and it is why duration is in
    `REFERENCEABLE_TYPES`."""
    col = await _add_duration_column(client, table_uuid)
    for value in (5400, 1800):
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {col: value}}
        )

    created = await client.post(
        f"/api/tables/{table_uuid}/metrics/",
        json={
            "name": "total_length",
            "display_name": "Total length",
            "config": {"formula": "SUM(length)"},
        },
    )
    assert created.status_code == 201, created.text

    body = (await client.get(f"/api/tables/{table_uuid}/metrics/values")).json()
    assert next(v["value"] for v in body["values"] if v["name"] == "total_length") == 7200
