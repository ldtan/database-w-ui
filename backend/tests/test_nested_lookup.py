"""Nested lookup columns: what you pick in lookup A constrains lookup B.

A lookup B nests under a sibling lookup A when B's target table has a lookup
column pointing back at A's target table (Cities have a Country; picking a
Country narrows the Cities). The pairing lives in B's config as
`parent_column_uuid` + `target_filter_column_uuid`, the options endpoint takes
an opt-in `parent_value` filter, and the write path enforces the pairing on
values being *added* — stale cells stay legal until the cell itself is edited,
the same rule choice options and kept lookup references follow.
"""


async def _named_table(client, workspace_uuid, add_column, name, display_name):
    """A table labelled by a primary `name` column; returns (table_uuid, name_col)."""
    table = (
        await client.post(
            "/api/tables/",
            json={
                "name": name,
                "display_name": display_name,
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    name_col = await add_column(table, name="name", display_name="Name", type="text")
    await client.patch(f"/api/tables/{table}", json={"primary_column_uuid": name_col})
    return table, name_col


async def _add_row(client, table, data):
    resp = await client.post(f"/api/tables/{table}/records/", json={"data": data})
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _scenario(client, workspace_uuid, table_uuid, add_column, **city_config):
    """Countries, Cities (each with a Country), and a table looking up both.

    The city lookup on `table_uuid` nests under the country lookup. Returns a
    dict of every uuid a test needs.
    """
    countries, country_name = await _named_table(
        client, workspace_uuid, add_column, "countries", "Countries"
    )
    japan = await _add_row(client, countries, {country_name: "Japan"})
    france = await _add_row(client, countries, {country_name: "France"})

    cities, city_name = await _named_table(
        client, workspace_uuid, add_column, "cities", "Cities"
    )
    city_country = await add_column(
        cities,
        name="country",
        display_name="Country",
        type="lookup",
        config={"target_table_uuid": countries},
    )
    tokyo = await _add_row(client, cities, {city_name: "Tokyo", city_country: japan})
    osaka = await _add_row(client, cities, {city_name: "Osaka", city_country: japan})
    paris = await _add_row(client, cities, {city_name: "Paris", city_country: france})

    country_col = await add_column(
        table_uuid,
        name="country",
        display_name="Country",
        type="lookup",
        config={"target_table_uuid": countries},
    )
    city_col = await add_column(
        table_uuid,
        name="city",
        display_name="City",
        type="lookup",
        config={
            "target_table_uuid": cities,
            "parent_column_uuid": country_col,
            "target_filter_column_uuid": city_country,
            **city_config,
        },
    )
    return {
        "countries": countries,
        "cities": cities,
        "city_country": city_country,
        "country_col": country_col,
        "city_col": city_col,
        "japan": japan,
        "france": france,
        "tokyo": tokyo,
        "osaka": osaka,
        "paris": paris,
    }


# ---- config validation ----


async def test_nesting_keys_must_be_set_together(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    for half in (
        {"parent_column_uuid": s["country_col"]},
        {"target_filter_column_uuid": s["city_country"]},
    ):
        resp = await client.post(
            f"/api/tables/{table_uuid}/columns/",
            json={
                "name": "half",
                "display_name": "Half",
                "type": "lookup",
                "config": {"target_table_uuid": s["cities"], **half},
            },
        )
        assert resp.status_code == 400, resp.text
        assert "set together" in resp.json()["detail"]


async def test_nesting_rejects_bad_parents(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    text_col = await add_column(table_uuid, name="note", display_name="Note", type="text")

    async def attempt(parent, filter_col):
        return await client.post(
            f"/api/tables/{table_uuid}/columns/",
            json={
                "name": "bad",
                "display_name": "Bad",
                "type": "lookup",
                "config": {
                    "target_table_uuid": s["cities"],
                    "parent_column_uuid": parent,
                    "target_filter_column_uuid": filter_col,
                },
            },
        )

    # Not a lookup column.
    resp = await attempt(text_col, s["city_country"])
    assert resp.status_code == 400 and "not a lookup" in resp.json()["detail"]
    # Not a column of this table at all (a Cities column).
    resp = await attempt(s["city_country"], s["city_country"])
    assert resp.status_code == 400, resp.text
    # A filter column that is not on the target table (it lives on this table).
    resp = await attempt(s["country_col"], s["country_col"])
    assert resp.status_code == 400, resp.text


async def test_nesting_rejects_self_and_mismatched_filter_target(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)

    # Self-nesting: the city column as its own parent.
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{s['city_col']}",
        json={
            "config": {
                "parent_column_uuid": s["city_col"],
                "target_filter_column_uuid": s["city_country"],
            }
        },
    )
    assert resp.status_code == 400, resp.text
    assert "itself" in resp.json()["detail"]

    # A filter column that is a lookup, but at the wrong table: Cities gets a
    # lookup at Cities, which no Country selection could ever match.
    wrong = await add_column(
        s["cities"],
        name="twin",
        display_name="Twin city",
        type="lookup",
        config={"target_table_uuid": s["cities"]},
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{s['city_col']}",
        json={
            "config": {
                "parent_column_uuid": s["country_col"],
                "target_filter_column_uuid": wrong,
            }
        },
    )
    assert resp.status_code == 400, resp.text
    assert "same table" in resp.json()["detail"]


async def test_nesting_rejects_a_cycle(client, workspace_uuid, table_uuid, add_column):
    """Two tables pointing at each other allow A-under-B *or* B-under-A — not both."""
    x, _ = await _named_table(client, workspace_uuid, add_column, "xs", "Xs")
    y, _ = await _named_table(client, workspace_uuid, add_column, "ys", "Ys")
    x_to_y = await add_column(
        x, name="y", display_name="Y", type="lookup", config={"target_table_uuid": y}
    )
    y_to_x = await add_column(
        y, name="x", display_name="X", type="lookup", config={"target_table_uuid": x}
    )
    a = await add_column(
        table_uuid,
        name="a",
        display_name="A",
        type="lookup",
        config={"target_table_uuid": x},
    )
    b = await add_column(
        table_uuid,
        name="b",
        display_name="B",
        type="lookup",
        config={"target_table_uuid": y},
    )

    ok = await client.patch(
        f"/api/tables/{table_uuid}/columns/{a}",
        json={
            "config": {"parent_column_uuid": b, "target_filter_column_uuid": x_to_y}
        },
    )
    assert ok.status_code == 200, ok.text

    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{b}",
        json={
            "config": {"parent_column_uuid": a, "target_filter_column_uuid": y_to_x}
        },
    )
    assert resp.status_code == 400, resp.text
    assert "cycle" in resp.json()["detail"]


async def test_nesting_survives_a_patch_that_does_not_resend_it(
    client, workspace_uuid, table_uuid, add_column
):
    """Config PATCHes merge; explicit nulls are how a pairing is removed."""
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)

    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{s['city_col']}",
        json={"config": {"required": True}},
    )
    assert resp.status_code == 200, resp.text
    config = resp.json()["config"]
    assert config["parent_column_uuid"] == s["country_col"]
    assert config["target_filter_column_uuid"] == s["city_country"]

    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{s['city_col']}",
        json={
            "config": {
                "parent_column_uuid": None,
                "target_filter_column_uuid": None,
            }
        },
    )
    assert resp.status_code == 200, resp.text
    config = resp.json()["config"]
    assert config["parent_column_uuid"] is None
    assert config["target_filter_column_uuid"] is None


# ---- options filtering ----


async def _labels(client, table_uuid, column, query=""):
    resp = await client.get(f"/api/tables/{table_uuid}/columns/{column}/options?{query}")
    assert resp.status_code == 200, resp.text
    return sorted(o["label"] for o in resp.json())


async def test_options_filter_by_parent_value(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)

    japan_only = await _labels(
        client, table_uuid, s["city_col"], f"parent_value={s['japan']}"
    )
    assert japan_only == ["Osaka", "Tokyo"]

    # Several parent values union — a multi-valued parent cell offers all its rows.
    both = await _labels(
        client,
        table_uuid,
        s["city_col"],
        f"parent_value={s['japan']}&parent_value={s['france']}",
    )
    assert both == ["Osaka", "Paris", "Tokyo"]

    # The filter composes with the label search.
    searched = await _labels(
        client, table_uuid, s["city_col"], f"q=os&parent_value={s['japan']}"
    )
    assert searched == ["Osaka"]

    # Without the param the search is what it always was.
    assert await _labels(client, table_uuid, s["city_col"]) == [
        "Osaka",
        "Paris",
        "Tokyo",
    ]


async def test_options_filter_matches_multi_valued_filter_cells(
    client, workspace_uuid, table_uuid, add_column
):
    """The target's filter cell may itself hold several uuids; `?|` matches any."""
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    await client.patch(
        f"/api/tables/{s['cities']}/columns/{s['city_country']}",
        json={"config": {"multiple": True}},
    )
    # Geneva belongs to two countries at once (a border town, spiritually).
    cities_name = (
        await client.get(f"/api/tables/{s['cities']}/columns/")
    ).json()[0]["uuid"]
    await _add_row(
        client,
        s["cities"],
        {cities_name: "Geneva", s["city_country"]: [s["japan"], s["france"]]},
    )

    for parent in (s["japan"], s["france"]):
        labels = await _labels(
            client, table_uuid, s["city_col"], f"parent_value={parent}"
        )
        assert "Geneva" in labels


async def test_options_resolve_mode_ignores_the_parent_filter(
    client, workspace_uuid, table_uuid, add_column
):
    """A held value must always resolve to its label, however stale the pairing."""
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    resp = await client.get(
        f"/api/tables/{table_uuid}/columns/{s['city_col']}/options"
        f"?uuid={s['paris']}&parent_value={s['japan']}"
    )
    assert resp.status_code == 200, resp.text
    assert [o["label"] for o in resp.json()] == ["Paris"]


async def test_options_parent_value_is_ignored_on_an_unnested_column(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    labels = await _labels(
        client, table_uuid, s["country_col"], f"parent_value={s['japan']}"
    )
    assert labels == ["France", "Japan"]


# ---- write validation ----


async def test_write_accepts_a_matching_pair_and_rejects_a_mismatch(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)

    ok = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {s["country_col"]: s["japan"], s["city_col"]: s["tokyo"]}},
    )
    assert ok.status_code == 201, ok.text

    bad = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {s["country_col"]: s["france"], s["city_col"]: s["tokyo"]}},
    )
    assert bad.status_code == 400, bad.text
    assert "does not belong" in bad.json()["detail"]


async def test_write_requires_the_parent_to_be_set(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {s["city_col"]: s["tokyo"]}},
    )
    assert resp.status_code == 400, resp.text
    assert "country" in resp.json()["detail"]


async def test_write_validates_labels_too(
    client, workspace_uuid, table_uuid, add_column
):
    """The import path sends labels, which resolve to uuids before the check."""
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    ok = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {s["country_col"]: "Japan", s["city_col"]: "Tokyo"}},
    )
    assert ok.status_code == 201, ok.text

    bad = await client.post(
        f"/api/tables/{table_uuid}/records/",
        json={"data": {s["country_col"]: "France", s["city_col"]: "Tokyo"}},
    )
    assert bad.status_code == 400, bad.text


async def test_stale_values_survive_until_the_cell_itself_changes(
    client, workspace_uuid, table_uuid, add_column
):
    """Enabling nesting over existing data must not lock the records: untouched
    and kept values pass; only an *added* value is held to the pairing."""
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    note = await add_column(table_uuid, name="note", display_name="Note", type="text")

    # Un-nest, write a now-illegal pair, then nest again: a stale record.
    await client.patch(
        f"/api/tables/{table_uuid}/columns/{s['city_col']}",
        json={
            "config": {
                "parent_column_uuid": None,
                "target_filter_column_uuid": None,
            }
        },
    )
    record = (
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {s["country_col"]: s["france"], s["city_col"]: s["tokyo"]}},
        )
    ).json()["uuid"]
    renest = await client.patch(
        f"/api/tables/{table_uuid}/columns/{s['city_col']}",
        json={
            "config": {
                "parent_column_uuid": s["country_col"],
                "target_filter_column_uuid": s["city_country"],
            }
        },
    )
    assert renest.status_code == 200, renest.text

    # Editing an unrelated column leaves the stale pair alone.
    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}", json={"data": {note: "hello"}}
    )
    assert resp.status_code == 200, resp.text

    # Re-sending the same held value is a keep, not an add.
    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}",
        json={"data": {s["city_col"]: s["tokyo"]}},
    )
    assert resp.status_code == 200, resp.text

    # Changing the cell is an add, and the pairing now applies.
    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}",
        json={"data": {s["city_col"]: s["osaka"]}},
    )
    assert resp.status_code == 400, resp.text

    # Fixing the parent in the same request satisfies it: the check runs over
    # the merged record, whatever order the cells arrived in.
    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}",
        json={"data": {s["city_col"]: s["osaka"], s["country_col"]: s["japan"]}},
    )
    assert resp.status_code == 200, resp.text


async def test_multi_valued_child_checks_only_added_values(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(
        client, workspace_uuid, table_uuid, add_column, multiple=True
    )
    record = (
        await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={
                "data": {
                    s["country_col"]: s["japan"],
                    s["city_col"]: [s["tokyo"]],
                }
            },
        )
    ).json()["uuid"]

    # Adding a city of the selected country: fine, Tokyo is merely kept.
    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}",
        json={"data": {s["city_col"]: [s["tokyo"], s["osaka"]]}},
    )
    assert resp.status_code == 200, resp.text

    # Adding a city of another country is refused.
    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}",
        json={"data": {s["city_col"]: [s["tokyo"], s["paris"]]}},
    )
    assert resp.status_code == 400, resp.text


async def test_import_enforces_the_pairing_per_row(
    client, workspace_uuid, table_uuid, add_column
):
    """CSV rows go through the same validation; a bad pair is a row error, not
    a failed import."""
    await _scenario(client, workspace_uuid, table_uuid, add_column)
    csv = "country,city\nJapan,Tokyo\nFrance,Tokyo\n"
    resp = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 1
    assert "does not belong" in body["errors"][0]["message"]


# ---- cascades ----


async def _city_config(client, table_uuid, city_col):
    return (await client.get(f"/api/tables/{table_uuid}/columns/{city_col}")).json()[
        "config"
    ]


async def test_deleting_the_parent_column_unnests_the_child(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    resp = await client.delete(f"/api/tables/{table_uuid}/columns/{s['country_col']}")
    assert resp.status_code == 204, resp.text
    config = await _city_config(client, table_uuid, s["city_col"])
    assert config["parent_column_uuid"] is None
    assert config["target_filter_column_uuid"] is None


async def test_deleting_the_filter_column_unnests_across_tables(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    resp = await client.delete(f"/api/tables/{s['cities']}/columns/{s['city_country']}")
    assert resp.status_code == 204, resp.text
    config = await _city_config(client, table_uuid, s["city_col"])
    assert config["parent_column_uuid"] is None
    assert config["target_filter_column_uuid"] is None


async def test_retargeting_the_parent_unnests_the_child(
    client, workspace_uuid, table_uuid, add_column
):
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    other, _ = await _named_table(client, workspace_uuid, add_column, "other", "Other")
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{s['country_col']}",
        json={"config": {"target_table_uuid": other}},
    )
    assert resp.status_code == 200, resp.text
    config = await _city_config(client, table_uuid, s["city_col"])
    assert config["parent_column_uuid"] is None
    assert config["target_filter_column_uuid"] is None


async def test_deleting_the_parents_target_table_unnests_the_child(
    client, workspace_uuid, table_uuid, add_column
):
    """The pairing must not survive the table that gave it meaning.

    Deleting Countries blanks both halves' *other* end — the parent lookup on this
    table and the filter lookup on Cities — but not the child itself, whose own
    target (Cities) is alive and untouched. Left nested, the child would keep a
    pairing that resolves to nothing: the picker offers no rows, the write path
    refuses every selection as not belonging to the parent, and the column dialog
    cannot save it until somebody clears "Nest under" by hand. So the scrub un-nests
    dependents as it goes, the same cascade a deleted or retargeted parent triggers.
    """
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    assert (await client.delete(f"/api/tables/{s['countries']}")).status_code == 204

    config = await _city_config(client, table_uuid, s["city_col"])
    assert config["parent_column_uuid"] is None
    assert config["target_filter_column_uuid"] is None
    # And the child is an ordinary lookup again: Cities is still there, so picking
    # one is a write like any other rather than a rejection with no way to satisfy it.
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {s["city_col"]: s["tokyo"]}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][s["city_col"]] == s["tokyo"]


async def test_deleting_the_childs_own_target_table_clears_its_pairing_too(
    client, workspace_uuid, table_uuid, add_column
):
    """The other direction, and the one that pins the pairing keys in the scrub itself.

    Delete Cities and the child *is* in scope: its own target is the table that went.
    Both halves of the pairing are cleared by the scrub's own field table rather than
    by the outward cascade the previous test covers, so this is what fails if either
    key is dropped from that table.
    """
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    assert (await client.delete(f"/api/tables/{s['cities']}")).status_code == 204

    config = await _city_config(client, table_uuid, s["city_col"])
    assert config["target_table_uuid"] is None
    assert config["parent_column_uuid"] is None
    assert config["target_filter_column_uuid"] is None


# ---- cloning ----


async def test_clone_remaps_the_parent_but_not_the_filter_column(
    client, workspace_uuid, table_uuid, add_column
):
    """The parent is a sibling — cloned, so remapped. The filter column belongs
    to the target table, which the clone still points at — left alone."""
    s = await _scenario(client, workspace_uuid, table_uuid, add_column)
    resp = await client.post(
        f"/api/tables/{table_uuid}/clone", json={"with_records": False}
    )
    assert resp.status_code == 201, resp.text
    clone_uuid = resp.json()["uuid"]

    clone_cols = (await client.get(f"/api/tables/{clone_uuid}/columns/")).json()
    by_name = {c["name"]: c for c in clone_cols}
    city = by_name["city"]
    assert city["config"]["parent_column_uuid"] == by_name["country"]["uuid"]
    assert city["config"]["parent_column_uuid"] != s["country_col"]
    assert city["config"]["target_filter_column_uuid"] == s["city_country"]

    # And the clone's pairing actually works: a mismatched write is refused.
    bad = await client.post(
        f"/api/tables/{clone_uuid}/records/",
        json={
            "data": {
                by_name["country"]["uuid"]: s["france"],
                city["uuid"]: s["tokyo"],
            }
        },
    )
    assert bad.status_code == 400, bad.text
