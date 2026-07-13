"""A list's children can be addressed by name as well as by uuid.

`/lists/{list}/columns/first_name` is the same column as
`/lists/{list}/columns/019f…`. The two can never be confused — a name cannot parse
as a uuid — so the resolver simply tries the uuid first and falls back to the name.

The uuid stays canonical: it is what every response carries and what the frontend
uses. The name is an input convenience, and it moves when you rename the column,
which these tests pin down rather than treat as an accident.
"""
import pytest


@pytest.fixture
def by_ref(client, list_uuid):
    """`/api/lists/{list}/{kind}/{ref}` for whichever child we're addressing."""

    def _url(kind: str, ref: str) -> str:
        return f"/api/lists/{list_uuid}/{kind}/{ref}"

    return _url


@pytest.fixture
async def view_uuid(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="status", display_name="Status", type="text")
    resp = await client.post(
        f"/api/lists/{list_uuid}/views/",
        json={
            "name": "open_only",
            "display_name": "Open only",
            "config": {
                "filters": {
                    "match": "all",
                    "conditions": [
                        {"column_uuid": col, "operator": "eq", "value": "open"}
                    ],
                }
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return col, resp.json()["uuid"]


async def test_a_column_answers_to_its_name(client, list_uuid, add_column, by_ref):
    uuid = await add_column(
        list_uuid, name="first_name", display_name="First name", type="text"
    )

    fetched = await client.get(by_ref("columns", "first_name"))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["uuid"] == uuid  # the uuid is still what comes back

    patched = await client.patch(
        by_ref("columns", "first_name"), json={"display_name": "Given name"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["display_name"] == "Given name"

    assert (await client.delete(by_ref("columns", "first_name"))).status_code == 204
    assert (await client.get(by_ref("columns", uuid))).status_code == 404


async def test_a_view_and_a_form_answer_to_their_names(
    client, list_uuid, add_column, by_ref
):
    col = await add_column(list_uuid, name="note", display_name="Note", type="text")
    view = await client.post(
        f"/api/lists/{list_uuid}/views/",
        json={"name": "recent", "display_name": "Recent"},
    )
    form = await client.post(
        f"/api/lists/{list_uuid}/forms/",
        json={
            "name": "intake",
            "display_name": "Intake",
            "config": {"fields": [{"column_uuid": col}]},
        },
    )
    assert (view.status_code, form.status_code) == (201, 201), (view.text, form.text)

    assert (await client.get(by_ref("views", "recent"))).json()["uuid"] == view.json()[
        "uuid"
    ]
    assert (await client.get(by_ref("forms", "intake"))).json()["uuid"] == form.json()[
        "uuid"
    ]
    assert (await client.delete(by_ref("views", "recent"))).status_code == 204
    assert (await client.delete(by_ref("forms", "intake"))).status_code == 204


async def test_lookup_options_take_a_column_name(
    client, workspace_uuid, list_uuid, add_column
):
    """The route with something after the ref, which is where a sloppy path
    definition would swallow the wrong segment."""
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
    label = await add_column(target, name="name", display_name="Name", type="text")
    await client.post(f"/api/lists/{target}/items/", json={"data": {label: "Ogdenville"}})
    await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )

    resp = await client.get(f"/api/lists/{list_uuid}/columns/school/options")
    assert resp.status_code == 200, resp.text
    assert [o["label"] for o in resp.json()] == ["Ogdenville"]


async def test_items_apply_a_view_named_in_the_query(client, list_uuid, view_uuid):
    col, uuid = view_uuid
    for value in ("open", "closed"):
        created = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        assert created.status_code == 201, created.text

    assert len((await client.get(f"/api/lists/{list_uuid}/items/")).json()) == 2
    for ref in (uuid, "open_only"):
        filtered = await client.get(f"/api/lists/{list_uuid}/items/?view_uuid={ref}")
        assert filtered.status_code == 200, filtered.text
        assert [i["data"][col] for i in filtered.json()] == ["open"]


async def test_a_name_never_resolves_to_a_deleted_child(
    client, list_uuid, add_column, by_ref
):
    """The unique index on `name` is partial (`WHERE deleted_at IS NULL`), so a
    deleted `notes` and a live `notes` coexist happily. If the resolver ignored
    `deleted_at` it could hand back the one in the bin — and, worse, hand it back
    for a column the caller has just recreated."""
    first = await add_column(list_uuid, name="notes", display_name="Notes", type="text")
    assert (await client.delete(by_ref("columns", first))).status_code == 204

    assert (await client.get(by_ref("columns", "notes"))).status_code == 404

    second = await add_column(
        list_uuid, name="notes", display_name="Notes again", type="text"
    )
    resolved = await client.get(by_ref("columns", "notes"))
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["uuid"] == second  # the live one, not its predecessor


async def test_renaming_a_column_moves_its_name_url(
    client, list_uuid, add_column, by_ref
):
    """A name in a URL is a link a rename can break; that is the slug bargain, and
    the reason the uuid remains the identifier anything durable should hold."""
    uuid = await add_column(list_uuid, name="phone", display_name="Phone", type="text")
    renamed = await client.patch(by_ref("columns", "phone"), json={"name": "mobile"})
    assert renamed.status_code == 200, renamed.text

    assert (await client.get(by_ref("columns", "phone"))).status_code == 404
    assert (await client.get(by_ref("columns", "mobile"))).json()["uuid"] == uuid
    assert (await client.get(by_ref("columns", uuid))).status_code == 200  # unmoved


async def test_a_ref_that_is_neither_a_uuid_nor_a_name_is_a_404(client, by_ref):
    """Not a 422: with the path typed as a string there is no parse to fail, and
    "no such column" is the honest answer for a name nobody has used."""
    resp = await client.get(by_ref("columns", "no_such_column"))
    assert resp.status_code == 404, resp.text


async def test_a_name_is_scoped_to_its_own_list(
    client, workspace_uuid, list_uuid, add_column
):
    """Names are unique per list, so the same `title` exists in both. Reaching the
    wrong list's column through the ref would be a cross-list read."""
    other = (
        await client.post(
            "/api/lists/",
            json={
                "name": "l2",
                "display_name": "List 2",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    mine = await add_column(list_uuid, name="title", display_name="Mine", type="text")
    theirs = await add_column(other, name="title", display_name="Theirs", type="text")
    assert mine != theirs

    resolved = await client.get(f"/api/lists/{list_uuid}/columns/title")
    assert resolved.json()["uuid"] == mine

    # And a *uuid* from the other list is still not reachable through this one.
    crossed = await client.get(f"/api/lists/{list_uuid}/columns/{theirs}")
    assert crossed.status_code == 404, crossed.text
