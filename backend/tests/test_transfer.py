"""Streaming export and bulk import of records."""


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
    body = "school\n" + "".join(f"School {i % 20}\n" for i in range(1200))
    imported = await client.post(
        f"/api/lists/{list_uuid}/import", files={"file": ("rows.csv", body, "text/csv")}
    )
    assert imported.json()["created"] == 1200, imported.text

    export = await client.post(f"/api/lists/{list_uuid}/export", json={"format": "csv"})
    assert export.status_code == 200, export.text
    lines = export.text.strip().splitlines()
    assert len(lines) == 1201  # header + every row
    assert all(line.startswith("School ") for line in lines[1:])  # no uuid leaked
