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
    assert lines[0] == "name,age"  # column names, not display names
    assert set(lines[1:]) == {"Ann,30", "Bo,25"}


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
