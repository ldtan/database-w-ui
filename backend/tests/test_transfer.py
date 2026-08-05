"""Streaming export and bulk import of records."""
import json

from src.core.config import settings


async def _seed(client, list_uuid, add_column):
    name = await add_column(
        list_uuid, name="name", display_name="Name", type="text",
        config={"unique": True},
    )
    age = await add_column(list_uuid, name="age", display_name="Age", type="number")
    return name, age


async def test_export_csv_uses_column_names_and_streams(client, list_uuid, add_column):
    name, age = await _seed(client, list_uuid, add_column)
    await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {name: "Ann", age: 30}})
    await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {name: "Bo", age: 25}})

    resp = await client.post(
        f"/api/lists/{list_uuid}/export", json={"format": "csv"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0] == "name,age,created_at,updated_at,created_by,updated_by"  # column names, not display names
    # Ignore default columns (created_at, updated_at, created_by, updated_by) and just check the user data.
    actual_rows = lines[1:]
    expected_rows = ["Ann,30", "Bo,25"]
    assert all(actual_rows[i].startswith(expected_rows[i]) for i in range(2))


async def test_export_respects_columns_sort_and_limit(client, list_uuid, add_column):
    name, age = await _seed(client, list_uuid, add_column)
    for n, a in [("Ann", 30), ("Bo", 25), ("Cy", 40)]:
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {name: n, age: a}})

    resp = await client.post(
        f"/api/lists/{list_uuid}/export",
        json={
            "format": "json",
            "columns": ["name"],
            "sorts": [{"column_uuid": age, "direction": "desc"}],
            "limit": 2,
        },
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert rows == [{"name": "Cy"}, {"name": "Ann"}]  # age desc, capped at 2


async def test_import_csv_creates_rows_and_reports_errors(client, list_uuid, add_column):
    await _seed(client, list_uuid, add_column)
    # Third row duplicates "Ann" on a unique column -> isolated error on line 4.
    csv = "name,age\nAnn,30\nBo,25\nAnn,99\n"
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("people.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2
    assert len(body["errors"]) == 1
    assert body["errors"][0]["row"] == 4  # header is line 1

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert len(items) == 2


async def test_import_csv_row_number_accounts_for_blank_lines(
    client, list_uuid, add_column
):
    await _seed(client, list_uuid, add_column)
    # Blank line 3; the duplicate "Ann" is on file line 4.
    csv = "name,age\nAnn,30\n\nAnn,99\n"
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("people.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["errors"][0]["row"] == 4  # the actual source line, not 3


async def test_import_extension_wins_over_format_field(client, list_uuid, add_column):
    await _seed(client, list_uuid, add_column)
    # A JSON file mislabeled as csv must still be parsed as JSON.
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        data={"format": "csv"},
        files={
            "file": (
                "people.json",
                '[{"name": "Ann", "age": 30}]',
                "application/json",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1


async def _seed_lookup(client, add_column, workspace_uuid, list_uuid, row_names):
    """A target list labelled by its primary column, and a lookup column at it.

    Returns (lookup column uuid, {row label: row uuid})."""
    target = (
        await client.post(
            "/api/lists/",
            json={
                "name": "schools",
                "display_name": "Schools",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    label_col = await add_column(
        target, name="name", display_name="Name", type="text"
    )
    resp = await client.patch(
        f"/api/lists/{target}", json={"primary_column_uuid": label_col}
    )
    assert resp.status_code == 200, resp.text

    rows = {}
    for row_name in row_names:
        created = await client.post(
            f"/api/lists/{target}/items/", json={"data": {label_col: row_name}}
        )
        assert created.status_code == 201, created.text
        rows[row_name] = created.json()["uuid"]

    lookup = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    return lookup, rows


async def test_export_writes_lookup_labels_and_import_reads_them_back(
    client, workspace_uuid, list_uuid, add_column
):
    """A row uuid in a CSV means nothing to a human, so a lookup exports the row's
    label — the value of the column it is displayed by — and import resolves it."""
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    lookup, rows = await _seed_lookup(
        client, add_column, workspace_uuid, list_uuid, ["Springfield High"]
    )
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {name: "Ann", lookup: rows["Springfield High"]}},
    )

    export = await client.post(f"/api/lists/{list_uuid}/export", json={"format": "csv"})
    assert export.status_code == 200, export.text
    csv = export.text
    assert "Springfield High" in csv
    assert rows["Springfield High"] not in csv  # the uuid must not leak into the file

    reimport = await client.post(
        f"/api/lists/{list_uuid}/import", files={"file": ("rows.csv", csv, "text/csv")}
    )
    assert reimport.status_code == 200, reimport.text
    assert reimport.json()["created"] == 1

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    # Both the original row and the re-imported one point at the same target row.
    assert [i["data"][lookup] for i in items] == [rows["Springfield High"]] * 2


async def test_import_rejects_an_ambiguous_lookup_label(
    client, workspace_uuid, list_uuid, add_column
):
    """Row labels are not unique, so a label matching two rows has no right answer:
    it must be an error, not a coin flip."""
    lookup, _ = await _seed_lookup(
        client, add_column, workspace_uuid, list_uuid, ["Twin", "Twin"]
    )

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {lookup: "Twin"}}
    )
    assert resp.status_code == 400, resp.text
    assert "more than one row" in resp.json()["detail"]


async def test_import_json_typed_values(client, list_uuid, add_column):
    name, age = await _seed(client, list_uuid, add_column)
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={
            "file": (
                "people.json",
                '[{"name": "Ann", "age": 30}, {"name": "Bo", "age": 25}]',
                "application/json",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 2
    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    ages = sorted(it["data"][age] for it in items)
    assert ages == [25, 30]  # numbers, not strings


async def test_a_label_with_surrounding_space_round_trips(
    client, workspace_uuid, list_uuid, add_column
):
    """Export writes a row's label verbatim, so import must match it verbatim. A
    value trimmed on the way in would fail to find the row it came from — breaking
    the round-trip on a file this app itself wrote."""
    lookup, rows = await _seed_lookup(
        client, add_column, workspace_uuid, list_uuid, [" Springfield High"]
    )
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {lookup: rows[" Springfield High"]}},
    )

    csv = (
        await client.post(f"/api/lists/{list_uuid}/export", json={"format": "csv"})
    ).text
    assert " Springfield High" in csv

    reimport = await client.post(
        f"/api/lists/{list_uuid}/import", files={"file": ("rows.csv", csv, "text/csv")}
    )
    assert reimport.status_code == 200, reimport.text
    assert reimport.json() == {"created": 1, "errors": []}

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [i["data"][lookup] for i in items] == [rows[" Springfield High"]] * 2


async def test_ambiguity_is_detected_without_loading_every_match(
    client, workspace_uuid, list_uuid, add_column
):
    """Detecting "this label is ambiguous" needs one row of result, not one per
    match: a label shared by a million target rows must not be materialized just to
    raise the error."""
    lookup, _ = await _seed_lookup(
        client, add_column, workspace_uuid, list_uuid, ["Twin"] * 50
    )

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {lookup: "Twin"}}
    )
    assert resp.status_code == 400, resp.text
    assert "more than one row" in resp.json()["detail"]


async def test_export_larger_than_a_batch_resolves_labels_mid_stream(
    client, workspace_uuid, list_uuid, add_column
):
    """Labels are fetched on the export's own session, between fetches from an open
    server-side cursor — a second session would double the connections each export
    holds, which is how concurrent exports deadlock the pool. This drives more rows
    than one export batch, so the resolver really does query mid-stream."""
    lookup, rows = await _seed_lookup(
        client, add_column, workspace_uuid, list_uuid, [f"School {i}" for i in range(20)]
    )
    # More than _EXPORT_BATCH (500), so labels load repeatedly while streaming.
    # Seeded in two files because one import is capped at IMPORT_MAX_ROWS — which is
    # the cap's own message tells anyone with a large file to do. Nothing here is
    # about import; it just fills the list faster than 1200 POSTs would.
    for start in (0, 600):
        body = "school\n" + "".join(
            f"School {i % 20}\n" for i in range(start, start + 600)
        )
        imported = await client.post(
            f"/api/lists/{list_uuid}/import",
            files={"file": ("rows.csv", body, "text/csv")},
        )
        assert imported.json()["created"] == 600, imported.text

    export = await client.post(f"/api/lists/{list_uuid}/export", json={"format": "csv"})
    assert export.status_code == 200, export.text
    lines = export.text.strip().splitlines()
    assert len(lines) == 1201  # header + every row
    assert all(line.startswith("School ") for line in lines[1:])  # no uuid leaked


async def _seed_calculated(client, list_uuid, add_column):
    """`qty` and `price`, plus a `total` computed from them."""
    qty = await add_column(list_uuid, name="qty", display_name="Qty", type="number")
    price = await add_column(
        list_uuid, name="price", display_name="Price", type="number"
    )
    total = await add_column(
        list_uuid,
        name="total",
        display_name="Total",
        type="calculated",
        config={"formula": "qty * price"},
    )
    return qty, price, total


async def test_export_carries_the_calculated_value(client, list_uuid, add_column):
    """A calculated column holds a value now, so the export writes it.

    It used to be dropped even when named explicitly, because exporting it would have
    written a blank heading. The file still round-trips: `_parse_cell` skips the column
    on the way back in and the formula rewrites the cell from the row's own values.
    """
    qty, price, _ = await _seed_calculated(client, list_uuid, add_column)
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {qty: 3, price: 10}}
    )

    resp = await client.post(
        f"/api/lists/{list_uuid}/export",
        json={"format": "json", "columns": ["qty", "price", "total"]},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert list(rows[0]) == ["qty", "price", "total"]
    assert rows[0]["total"] == 30


async def test_export_computes_rather_than_reading_the_stored_index(
    client, list_uuid, add_column, session_factory
):
    """An export is the one read that leaves the app, so it must not trust the index.

    The stored copy is written for Postgres to filter and sort on and can lag a formula
    edit. A file nothing can correct afterwards has to carry the formula's real answer,
    so the export shapes each row exactly as the grid does.
    """
    import sqlalchemy as sa

    qty, price, total = await _seed_calculated(client, list_uuid, add_column)
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {qty: 3, price: 10}}
    )

    # Corrupt the index behind the service's back, the way a stale one would look.
    async with session_factory() as db:
        await db.execute(
            sa.text(
                "UPDATE list_items "
                "SET data = jsonb_set(data, ARRAY[:key], '999'::jsonb)"
            ),
            {"key": total},
        )
        await db.commit()

    resp = await client.post(
        f"/api/lists/{list_uuid}/export", json={"format": "json"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["total"] == 30  # recomputed, not the 999 on disk


async def test_import_ignores_a_value_for_a_calculated_column(
    client, list_uuid, add_column
):
    """A hand-made file may still carry the column. Its value is dropped rather
    than written or raised: the formula decides what `total` is, and one stray
    heading must not fail an otherwise good row."""
    _, _, total = await _seed_calculated(client, list_uuid, add_column)

    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", "qty,price,total\n3,10,999\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 1, "errors": []}

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert items[0]["data"][total] == 30  # the formula's answer, not the file's 999


async def test_import_refuses_a_file_over_the_row_cap_without_writing_any_of_it(
    client, list_uuid, add_column, monkeypatch
):
    """Every row is several round trips, so the file has to be bounded somewhere.
    The cap is checked before the first insert, which is the part worth pinning: a
    file one row too long has to leave the list exactly as it was, or "split it and
    import each part separately" would silently mean "and skip whatever already
    went in"."""
    monkeypatch.setattr(settings, "IMPORT_MAX_ROWS", 3)
    name, age = await _seed(client, list_uuid, add_column)

    body = "name,age\n" + "".join(f"P{i},{i}\n" for i in range(4))
    resp = await client.post(
        f"/api/lists/{list_uuid}/import", files={"file": ("rows.csv", body, "text/csv")}
    )
    assert resp.status_code == 400, resp.text
    assert "more than 3 rows" in resp.json()["detail"]
    assert (await client.get(f"/api/lists/{list_uuid}/items/")).json() == []

    # The cap itself is allowed through, so the message names a limit that works.
    body = "name,age\n" + "".join(f"P{i},{i}\n" for i in range(3))
    resp = await client.post(
        f"/api/lists/{list_uuid}/import", files={"file": ("rows.csv", body, "text/csv")}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 3


async def test_import_refuses_a_file_over_the_row_cap_as_json_too(
    client, list_uuid, add_column, monkeypatch
):
    """The cap lives in `import_records` rather than in either parser, so both
    upload formats get it from one place."""
    monkeypatch.setattr(settings, "IMPORT_MAX_ROWS", 2)
    await _seed(client, list_uuid, add_column)

    body = json.dumps([{"name": f"P{i}", "age": i} for i in range(3)])
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.json", body, "application/json")},
    )
    assert resp.status_code == 400, resp.text
    assert "more than 2 rows" in resp.json()["detail"]
    assert (await client.get(f"/api/lists/{list_uuid}/items/")).json() == []


async def test_import_refuses_an_upload_over_the_size_cap(
    client, list_uuid, add_column, monkeypatch
):
    """The row cap alone bounds the database work but not the memory: the file is
    read whole before anything can count its rows. A single enormous row would pass
    a row cap and still be a request that cannot be served."""
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1024 * 1024)
    name, _ = await _seed(client, list_uuid, add_column)

    body = "name,age\n" + "x" * (2 * 1024 * 1024) + ",1\n"
    resp = await client.post(
        f"/api/lists/{list_uuid}/import", files={"file": ("rows.csv", body, "text/csv")}
    )
    assert resp.status_code == 400, resp.text
    assert "larger than 1 MB" in resp.json()["detail"]
    assert (await client.get(f"/api/lists/{list_uuid}/items/")).json() == []


async def test_import_answers_an_undecodable_file_rather_than_failing(
    client, list_uuid, add_column
):
    """A file saved in the wrong encoding is the user's mistake to fix, so it gets a
    sentence saying so — it used to escape the handler and become a 500."""
    await _seed(client, list_uuid, add_column)

    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", b"name,age\n\xff\xfe\x00bad,1\n", "text/csv")},
    )
    assert resp.status_code == 400, resp.text
    assert "UTF-8" in resp.json()["detail"]
