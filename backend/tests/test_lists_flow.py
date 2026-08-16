"""End-to-end tests over the HTTP API: workspaces, lists, columns, records."""
from datetime import datetime, timezone
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import func, select

from src.auth.users.models import User
from src.core.config import settings
from src.lists.columns.models import Column
from src.lists.items.schemas import BulkDeleteRequest
from src.lists.forms.models import Form
from src.lists.items.models import ListItem
from src.lists.models import List
from src.lists.views.models import View


async def _child_counts(session_factory, list_uuid: str):
    """Return {child: (total, active)} row counts for a list's descendants."""
    async with session_factory() as db:
        list_id = await db.scalar(
            select(List.id).where(List.uuid == UUID(list_uuid))
        )
        counts = {}
        for name, model in [
            ("columns", Column),
            ("items", ListItem),
            ("views", View),
            ("forms", Form),
        ]:
            total = await db.scalar(
                select(func.count()).select_from(model).where(model.list_id == list_id)
            )
            active = await db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.list_id == list_id, model.deleted_at.is_(None))
            )
            counts[name] = (total, active)
        return counts


async def test_delete_list_cascades_soft_delete(
    client, session_factory, list_uuid, add_column
):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "x"}})

    before = await _child_counts(session_factory, list_uuid)
    assert all(total > 0 and active > 0 for total, active in before.values())

    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204

    after = await _child_counts(session_factory, list_uuid)
    for name, (total, active) in after.items():
        assert total > 0, f"{name} rows were hard-deleted"  # soft delete keeps rows
        assert active == 0, f"{name} not soft-deleted on cascade"


async def test_delete_list_scrubs_cross_list_lookup_refs(
    client, workspace_uuid, add_column
):
    async def make_list(name):
        resp = await client.post(
            "/api/lists/",
            json={"name": name, "display_name": name, "workspace_uuid": workspace_uuid},
        )
        return resp.json()["uuid"]

    authors = await make_list("authors")
    aname = await add_column(authors, name="full_name", display_name="Name", type="text")
    a1 = (
        await client.post(
            f"/api/lists/{authors}/items/", json={"data": {aname: "Ursula"}}
        )
    ).json()["uuid"]

    books = await make_list("books")
    author_col = await add_column(
        books,
        name="author",
        display_name="Author",
        type="lookup",
        # Every field the scrub is meant to clear is set to something non-null
        # here, so that dropping one from the tuple in
        # `_scrub_references_to_lists` fails this test rather than passing on a
        # key that was never set in the first place.
        config={
            "target_list_uuid": authors,
            "display_column_uuid": aname,
            "option_filters": {
                "conjunction": "and",
                "conditions": [
                    {"column_uuid": aname, "operator": "is_not_empty"}
                ],
            },
            "required": True,
        },
    )
    book = (
        await client.post(
            f"/api/lists/{books}/items/", json={"data": {author_col: a1}}
        )
    ).json()
    assert book["data"][author_col] == a1

    # Deleting the target list must scrub references held by the surviving list.
    assert (await client.delete(f"/api/lists/{authors}")).status_code == 204

    items = (await client.get(f"/api/lists/{books}/items/")).json()
    assert len(items) == 1
    assert author_col not in items[0]["data"]  # dangling reference removed

    # And the column's *config* goes with the values it described. Leaving
    # `target_list_uuid` naming a list that no longer exists left the column
    # unusable and unrepairable: the options endpoint 404'd, so the picker and
    # every label resolution failed, and the dialog showed the dead uuid as "…"
    # under a hint about lookup-only sharing.
    column = (
        await client.get(f"/api/lists/{books}/columns/{author_col}")
    ).json()
    assert column["config"]["target_list_uuid"] is None
    assert column["config"]["display_column_uuid"] is None
    assert column["config"]["option_filters"] is None
    # `required` too, and this one is not tidiness. It is enforced over the merged
    # record on every write, and the cell has just been emptied and cannot be
    # refilled, so leaving it on would freeze creates and edits across this whole
    # list — see the next test.
    assert column["config"]["required"] is False


async def test_a_required_lookup_stops_freezing_the_list_when_its_target_goes(
    client, workspace_uuid, add_column
):
    """Deleting one list must not stop writes to another.

    `required` is enforced by `_validate_data` over the *merged* record, for every
    column, on every create and update. Once the target list is gone the cell is
    empty and unfillable — the picker offers nothing and any uuid is refused — so a
    required lookup left ticked refuses every write to the list that holds it. Nor
    could the tick be removed on its own: a config PATCH merges onto what is stored,
    so `target_list_uuid: None` comes back and the save is refused for that instead.
    The scrub clears it, on the same rule that refuses `unique` + `multiple` — a
    column must not claim a guarantee it cannot enforce.
    """
    async def make_list(name):
        resp = await client.post(
            "/api/lists/",
            json={"name": name, "display_name": name, "workspace_uuid": workspace_uuid},
        )
        return resp.json()["uuid"]

    authors = await make_list("authors")
    aname = await add_column(authors, name="full_name", display_name="Name", type="text")
    ursula = (
        await client.post(
            f"/api/lists/{authors}/items/", json={"data": {aname: "Ursula"}}
        )
    ).json()["uuid"]

    books = await make_list("books")
    title = await add_column(books, name="title", display_name="Title", type="text")
    author_col = await add_column(
        books,
        name="author",
        display_name="Author",
        type="lookup",
        config={"target_list_uuid": authors, "required": True},
    )
    existing = (
        await client.post(
            f"/api/lists/{books}/items/",
            json={"data": {title: "The Dispossessed", author_col: ursula}},
        )
    ).json()["uuid"]

    assert (await client.delete(f"/api/lists/{authors}")).status_code == 204

    # A new record, with no author to give it.
    created = await client.post(
        f"/api/lists/{books}/items/", json={"data": {title: "The Left Hand"}}
    )
    assert created.status_code == 201, created.text

    # And an edit to an unrelated cell of a record whose author was just scrubbed.
    edited = await client.patch(
        f"/api/lists/{books}/items/{existing}",
        json={"data": {title: "The Dispossessed (rev.)"}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"][title] == "The Dispossessed (rev.)"


async def test_a_lookup_whose_target_was_deleted_still_reads_and_can_be_retargeted(
    client, workspace_uuid, add_column
):
    """The repair path, which is the whole point of clearing the config.

    A column left pointing at a deleted list was not merely wrong, it was stuck: the
    options endpoint raised, so nothing could render it, and `_validate_config`
    refused every PATCH that resent the config — which the column dialog always does
    — so retargeting through the UI was the one thing that could not be done.
    """
    async def make_list(name):
        resp = await client.post(
            "/api/lists/",
            json={"name": name, "display_name": name, "workspace_uuid": workspace_uuid},
        )
        return resp.json()["uuid"]

    authors = await make_list("authors")
    await add_column(authors, name="full_name", display_name="Name", type="text")
    replacement = await make_list("people")
    await add_column(replacement, name="full_name", display_name="Name", type="text")

    books = await make_list("books")
    author_col = await add_column(
        books,
        name="author",
        display_name="Author",
        type="lookup",
        config={"target_list_uuid": authors},
    )

    assert (await client.delete(f"/api/lists/{authors}")).status_code == 204

    # The options endpoint answers "nothing to offer" rather than 404ing, so the
    # picker, the record dialog and every label resolution keep working.
    options = await client.get(f"/api/lists/{books}/columns/{author_col}/options")
    assert options.status_code == 200, options.text
    assert options.json() == []

    # The list itself still reads.
    assert (await client.get(f"/api/lists/{books}/items/")).status_code == 200

    # Retargeting works, and is the repair.
    fixed = await client.patch(
        f"/api/lists/{books}/columns/{author_col}",
        json={"config": {"target_list_uuid": replacement}},
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["config"]["target_list_uuid"] == replacement

    reopened = await client.get(f"/api/lists/{books}/columns/{author_col}/options")
    assert reopened.status_code == 200, reopened.text


async def test_a_target_less_lookup_still_refuses_to_be_saved_without_a_target(
    client, workspace_uuid, add_column
):
    """Clearing the config makes the column *fixable*, not valid.

    A lookup with nowhere to look is not a configuration worth storing, so saving one
    is still refused — the difference is that the picker now opens empty and the
    message names the thing to fix, where before it showed a dead uuid as "…" and
    refused for a reason nothing on screen explained."""
    async def make_list(name):
        resp = await client.post(
            "/api/lists/",
            json={"name": name, "display_name": name, "workspace_uuid": workspace_uuid},
        )
        return resp.json()["uuid"]

    authors = await make_list("authors")
    await add_column(authors, name="full_name", display_name="Name", type="text")
    books = await make_list("books")
    author_col = await add_column(
        books,
        name="author",
        display_name="Author",
        type="lookup",
        config={"target_list_uuid": authors},
    )
    assert (await client.delete(f"/api/lists/{authors}")).status_code == 204

    resp = await client.patch(
        f"/api/lists/{books}/columns/{author_col}",
        json={"config": {"help_text": "pick someone"}},
    )
    assert resp.status_code == 400
    assert "target_list_uuid" in resp.json()["detail"]


async def test_options_still_raise_for_a_target_that_is_named_but_missing(
    client, session_factory, workspace_uuid, add_column
):
    """The other half of the deliberate asymmetry, which nothing else covers.

    An *absent* target returns `[]`, because a deleted list produces that state
    ordinarily. A target that is *named* and cannot be found is a different thing:
    a hand-edited config, or a delete path that failed to scrub. Answering "no
    options" there would hide it, so it still raises — and without this test the
    branch that raises could be simplified away with a green suite.
    """
    lst = (
        await client.post(
            "/api/lists/",
            json={"name": "books", "display_name": "Books", "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    other = (
        await client.post(
            "/api/lists/",
            json={"name": "authors", "display_name": "Authors", "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    author_col = await add_column(
        lst,
        name="author",
        display_name="Author",
        type="lookup",
        config={"target_list_uuid": other},
    )

    # Written behind the service, because nothing the API offers can produce it —
    # which is the point of keeping it an error.
    async with session_factory() as db:
        column = await db.scalar(
            select(Column).where(Column.uuid == UUID(author_col))
        )
        column.config = {**column.config, "target_list_uuid": str(uuid7())}
        await db.commit()

    resp = await client.get(f"/api/lists/{lst}/columns/{author_col}/options")
    assert resp.status_code == 404, resp.text


async def test_delete_workspace_cascades_to_lists_and_descendants(
    client, session_factory, workspace_uuid, list_uuid, add_column
):
    await add_column(list_uuid, name="n", display_name="N", type="text")

    assert (await client.delete(f"/api/workspaces/{workspace_uuid}")).status_code == 204

    # The list itself is soft-deleted...
    async with session_factory() as db:
        deleted_at = await db.scalar(
            select(List.deleted_at).where(List.uuid == UUID(list_uuid))
        )
    assert deleted_at is not None

    # ...and so are its descendants.
    after = await _child_counts(session_factory, list_uuid)
    for name, (total, active) in after.items():
        assert active == 0, f"{name} not soft-deleted when workspace deleted"


async def test_new_list_autoprovisions_audit_columns(client, workspace_uuid):
    resp = await client.post(
        "/api/lists/",
        json={"name": "audit", "display_name": "Audit", "workspace_uuid": workspace_uuid},
    )
    assert resp.status_code == 201, resp.text
    list_uuid = resp.json()["uuid"]

    columns = (await client.get(f"/api/lists/{list_uuid}/columns/")).json()
    names = {col["name"] for col in columns}
    assert {"created_at", "updated_at", "created_by", "updated_by"} <= names

    created_at = next(col for col in columns if col["name"] == "created_at")
    updated_at = next(col for col in columns if col["name"] == "updated_at")
    created_by = next(col for col in columns if col["name"] == "created_by")
    updated_by = next(col for col in columns if col["name"] == "updated_by")

    assert created_at["type"] == "datetime"
    assert created_at["config"]["default"] == "now"
    assert created_at["config"]["read_only"] is True

    assert updated_at["type"] == "datetime"
    assert updated_at["config"]["onupdate"] == "now"
    assert updated_at["config"]["read_only"] is True

    assert created_by["type"] == "user"
    assert created_by["config"]["default"] == "current_user"
    assert created_by["config"]["read_only"] is True

    assert updated_by["type"] == "user"
    assert updated_by["config"]["onupdate"] == "current_user"
    assert updated_by["config"]["read_only"] is True


async def test_list_autoprovisions_view_and_forms(client, list_uuid):
    views = (await client.get(f"/api/lists/{list_uuid}/views/")).json()
    forms = (await client.get(f"/api/lists/{list_uuid}/forms/")).json()
    assert [v["name"] for v in views] == ["grid"]
    assert sorted(f["name"] for f in forms) == ["create", "update", "view"]
    assert all(f["is_default"] for f in forms)


async def test_view_form_starts_showing_every_column(client, list_uuid):
    """The display-only form seeds empty, which is the "all columns" convention.

    Create and update instead name the audit columns to keep them *off* the form —
    nobody types "created by", but it is exactly what someone opens a record to
    read. An empty layout also picks up calculated columns, which no input form
    may contain.
    """
    forms = (await client.get(f"/api/lists/{list_uuid}/forms/")).json()
    view_form = next(f for f in forms if f["name"] == "view")
    assert view_form["type"] == "view"
    assert view_form["is_default"] is True
    assert view_form["config"]["fields"] == []

    for name in ("create", "update"):
        form = next(f for f in forms if f["name"] == name)
        assert form["type"] == name
        # Audit columns are named-and-excluded, under the key FormConfig accepts:
        # `extra="forbid"` means a misspelling here 422s the first PATCH back.
        assert form["config"]["fields"]
        assert all(
            field["included"] is False and field["enabled"] is False
            for field in form["config"]["fields"]
        )


async def test_list_scoped_to_workspace(client, workspace_uuid, list_uuid):
    resp = await client.get("/api/lists/", params={"workspace_uuid": workspace_uuid})
    assert resp.status_code == 200
    assert [lst["uuid"] for lst in resp.json()] == [list_uuid]


async def test_column_name_must_be_slug(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={"name": "Not A Slug", "display_name": "X", "type": "text"},
    )
    assert resp.status_code == 422  # NameSlug pattern rejects spaces/caps


async def test_add_column_and_item_roundtrip(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="title", display_name="Title", type="text")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "hello"}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "hello"


async def test_bulk_delete_items(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")

    async def add(value):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        return resp.json()["uuid"]

    u1, u2 = await add("a"), await add("b")
    await add("c")

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete",
        json={"uuids": [u1, u2, "00000000-0000-0000-0000-000000000000"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 2  # bogus uuid skipped

    items = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [i["data"][col] for i in items] == ["c"]


async def test_bulk_delete_counts_a_repeated_uuid_once(client, list_uuid, add_column):
    """The count is what the UI reports back, so it has to be rows removed rather
    than uuids handled. Naming the same record twice used to add two to the total
    while deleting one."""
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "a"}}
    )
    uuid = resp.json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete", json={"uuids": [uuid, uuid]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1


async def test_bulk_delete_ignores_a_uuid_from_another_list(
    client, workspace_uuid, list_uuid, add_column
):
    """Permissions are now resolved from the uuid alone, without reference to which
    list the record is on, so the delete itself is what confines the batch. A record
    the caller owns elsewhere must not go just because it was named here."""
    resp = await client.post(
        "/api/lists/",
        json={"name": "l2", "display_name": "List 2", "workspace_uuid": workspace_uuid},
    )
    other_list = resp.json()["uuid"]
    other_col = await add_column(other_list, name="n", display_name="N", type="text")
    resp = await client.post(
        f"/api/lists/{other_list}/items/", json={"data": {other_col: "keep"}}
    )
    stranger = resp.json()["uuid"]

    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "go"}}
    )
    mine = resp.json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete", json={"uuids": [mine, stranger]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1

    survivors = (await client.get(f"/api/lists/{other_list}/items/")).json()
    assert [i["uuid"] for i in survivors] == [stranger]


def test_the_bulk_delete_cap_comes_from_config():
    """`max_length` is read once when the class is defined, so unlike the other two
    limits this one cannot be monkeypatched and the tests below use its default
    directly. Assert the wiring instead, or a future edit could quietly put the
    literal back and nothing would notice."""
    caps = [
        getattr(meta, "max_length", None)
        for meta in BulkDeleteRequest.model_fields["uuids"].metadata
    ]
    assert settings.BULK_DELETE_MAX in caps


async def test_bulk_delete_refuses_an_oversized_batch(client, list_uuid):
    """The cap is the only thing bounding this endpoint's work — nothing else on the
    request says how many uuids may arrive."""
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete",
        json={"uuids": [str(uuid7()) for _ in range(501)]},
    )
    assert resp.status_code == 422, resp.text

    # And the boundary itself is allowed through (none of them exist, so nothing
    # is deleted — the point is that it is accepted at all).
    resp = await client.post(
        f"/api/lists/{list_uuid}/items/bulk-delete",
        json={"uuids": [str(uuid7()) for _ in range(500)]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 0


async def test_required_rejected_when_missing(client, list_uuid, add_column):
    await add_column(
        list_uuid, name="title", display_name="Title", type="text",
        config={"required": True},
    )
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"].lower()


async def test_text_max_length_enforced(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="code", display_name="Code", type="text",
        config={"max_length": 3},
    )
    ok = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "abc"}})
    assert ok.status_code == 201
    bad = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "abcd"}})
    assert bad.status_code == 400


async def test_number_range_and_integer_only(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="qty", display_name="Qty", type="number",
        config={"min": 0, "max": 10, "integer_only": True},
    )
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 5}})).status_code == 201
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 50}})).status_code == 400
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 5.5}})).status_code == 400


async def test_temporal_range_enforced(client, list_uuid, add_column):
    """A date, time or datetime column may be bounded on either side.

    One test over all three because the bound is the same pair of config keys read
    through the type's own parser, and the interesting part is that each type's
    parser is the one doing the comparing — "09:00" is not a date and "2026-08-09"
    is not a time.
    """
    cases = [
        ("date", {"min": "2026-01-01", "max": "2026-12-31"}, "2026-08-09", "2025-12-31"),
        ("time", {"min": "09:00:00", "max": "17:00:00"}, "12:30:00", "08:59:00"),
        (
            "datetime",
            {"min": "2026-01-01T00:00:00", "max": "2026-12-31T23:59:59"},
            "2026-08-09T14:30:00",
            "2027-01-01T00:00:00",
        ),
    ]
    for type_, config, inside, outside in cases:
        col = await add_column(
            list_uuid, name=f"when_{type_}", display_name=type_, type=type_,
            config=config,
        )
        ok = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: inside}}
        )
        assert ok.status_code == 201, ok.text
        bad = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: outside}}
        )
        assert bad.status_code == 400, bad.text


async def test_temporal_range_must_be_well_formed(client, list_uuid):
    """The column itself is refused, rather than one that can never be filled in."""
    async def add(config):
        return await client.post(
            f"/api/lists/{list_uuid}/columns/",
            json={
                "name": "due", "display_name": "Due", "type": "date",
                "config": config,
            },
        )

    backwards = await add({"min": "2026-12-31", "max": "2026-01-01"})
    assert backwards.status_code == 400
    assert "cannot exceed" in backwards.json()["detail"]

    unparseable = await add({"min": "the first of January"})
    assert unparseable.status_code == 400

    # A default outside the column's own bounds is refused for the same reason:
    # every record it created would be invalid the moment anyone edited it.
    outside = await add({"min": "2026-01-01", "default": "2025-06-01"})
    assert outside.status_code == 400


async def test_choice_shorthand_expands_and_validates(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="status", display_name="Status", type="choice",
        config={"choices": "Todo;Doing;Done"},
    )
    column = (await client.get(f"/api/lists/{list_uuid}/columns/{col}")).json()
    assert column["config"]["options"] == ["Todo", "Doing", "Done"]
    # A cell stores the option itself — the visible label, exactly.
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "Todo"}})).status_code == 201
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "todo"}})).status_code == 400
    assert (await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "nope"}})).status_code == 400


async def test_choice_duplicate_options_rejected(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "status", "display_name": "Status", "type": "choice",
            "config": {"choices": "Todo;Todo"},
        },
    )
    assert resp.status_code == 400
    assert "unique" in resp.json()["detail"].lower()


async def test_choice_default_must_be_an_option(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "status", "display_name": "Status", "type": "choice",
            "config": {"choices": "Todo;Done", "default": "Doing"},
        },
    )
    assert resp.status_code == 400
    assert "not one of" in resp.json()["detail"].lower()


async def test_choice_config_patch_keeps_options(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="status", display_name="Status", type="choice",
        config={"choices": "Todo;Done"},
    )
    # A config PATCH that doesn't resend options must not wipe the set.
    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}",
        json={"config": {"required": True}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["options"] == ["Todo", "Done"]


async def test_dynamic_default_today(client, list_uuid, add_column):
    """With no zone recorded for the user, the stamp falls back to UTC — not to the
    server process's local time, which would make the value depend on where the box
    happens to be configured."""
    col = await add_column(
        list_uuid, name="due", display_name="Due", type="date",
        config={"default": "today"},
    )
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == datetime.now(timezone.utc).date().isoformat()


async def test_dynamic_default_today_uses_the_users_timezone(
    client, list_uuid, add_column, session_factory, user_id
):
    """The stamp follows the user's calendar rather than the server's clock.

    Two zones 25 hours apart, so their local dates differ no matter what time the
    suite runs: were the setting ignored, both records would carry the same date.
    Compared against each zone's own `today` rather than a fixed string, because the
    right answer moves with the clock.
    """
    col = await add_column(
        list_uuid, name="due", display_name="Due", type="date",
        config={"default": "today"},
    )

    stamped = {}
    for zone in ("Pacific/Kiritimati", "Pacific/Niue"):  # UTC+14 and UTC-11
        async with session_factory() as db:
            user = await db.get(User, user_id)
            # A new dict, not a mutation: JSONB is not mutation-tracked.
            user.settings = {**(user.settings or {}), "timezone": zone}
            await db.commit()

        resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
        assert resp.status_code == 201, resp.text
        stamped[zone] = resp.json()["data"][col]
        assert stamped[zone] == datetime.now(ZoneInfo(zone)).date().isoformat()

    assert stamped["Pacific/Kiritimati"] != stamped["Pacific/Niue"]


async def test_dynamic_now_is_stored_without_an_offset(
    client, list_uuid, add_column, session_factory, user_id
):
    """A stamped datetime has to be indistinguishable from a typed one.

    These columns hold a wall-clock reading, so resolving `now` in the user's zone
    must not leak that zone into the value: a stored offset would sort against the
    naive values beside it wrongly, and min/max comparison raises outright on the
    mixed convention (see validation.validate_scalar).
    """
    async with session_factory() as db:
        user = await db.get(User, user_id)
        user.settings = {**(user.settings or {}), "timezone": "Asia/Manila"}
        await db.commit()

    col = await add_column(
        list_uuid, name="seen", display_name="Seen", type="datetime",
        config={"default": "now"},
    )
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert resp.status_code == 201, resp.text

    value = resp.json()["data"][col]
    stamped = datetime.fromisoformat(value)
    assert stamped.tzinfo is None, value
    # And it reads as Manila's wall clock, not UTC's. As a delta rather than a
    # prefix match, so an hour boundary crossed mid-test cannot fail it; UTC would
    # be eight hours out, far outside the window.
    manila = datetime.now(ZoneInfo("Asia/Manila")).replace(tzinfo=None)
    assert abs((stamped - manila).total_seconds()) < 60, value


async def test_static_default_applied_when_empty(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="qty", display_name="Qty", type="number",
        config={"default": 7},
    )
    # Absent -> the default is filled in.
    empty = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {}})
    assert empty.status_code == 201, empty.text
    assert empty.json()["data"][col] == 7
    # A provided value wins over the default.
    given = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: 3}})
    assert given.json()["data"][col] == 3


async def test_static_default_must_be_valid(client, list_uuid):
    # A default that is not itself a valid value for the column is rejected up front.
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "qty", "display_name": "Qty", "type": "number",
            "config": {"default": 5, "max": 3},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_onupdate_stamps_on_create_and_edit(client, list_uuid, add_column):
    stamped = await add_column(
        list_uuid, name="touched", display_name="Touched", type="datetime",
        config={"onupdate": "now"},
    )
    other = await add_column(
        list_uuid, name="note", display_name="Note", type="text",
    )
    # Stamped at create even though nothing was sent for it.
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {other: "a"}}
    )
    assert created.status_code == 201, created.text
    first = created.json()["data"][stamped]
    assert first  # a concrete datetime string

    item_uuid = created.json()["uuid"]
    # Editing any field re-stamps it, and a value the client tries to set is ignored.
    edited = await client.patch(
        f"/api/lists/{list_uuid}/items/{item_uuid}",
        json={"data": {other: "b", stamped: "1999-01-01T00:00:00"}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"][stamped] != "1999-01-01T00:00:00"
    assert edited.json()["data"][stamped] >= first


async def test_onupdate_rejected_on_unsupported_type(client, list_uuid):
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "note", "display_name": "Note", "type": "text",
            "config": {"onupdate": "now"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "on-update" in resp.json()["detail"].lower()


async def test_unique_sequential_conflict(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="email", display_name="Email", type="text",
        config={"unique": True},
    )
    first = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "a@b.co"}})
    assert first.status_code == 201
    second = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "a@b.co"}})
    assert second.status_code == 409


async def test_config_patch_merges(client, list_uuid, add_column):
    col = await add_column(
        list_uuid, name="qty", display_name="Qty", type="number",
        config={"required": True, "min": 0, "max": 100},
    )
    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"max": 50}}
    )
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    # max updated, but required/min preserved (merge, not replace)
    assert cfg["max"] == 50
    assert cfg.get("required") is True
    assert cfg.get("min") == 0


async def test_lookup_default_rejected(client, list_uuid, workspace_uuid):
    target = (
        await client.post(
            "/api/lists/",
            json={"name": "target", "display_name": "Target", "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "link", "display_name": "Link", "type": "lookup",
            "config": {"target_list_uuid": target, "default": "x"},
        },
    )
    assert resp.status_code == 400
    assert "default" in resp.json()["detail"].lower()


async def test_deleting_a_row_keeps_the_lookup_reference(client, list_uuid, workspace_uuid, add_column):
    # Target list with one row.
    target = (
        await client.post(
            "/api/lists/",
            json={"name": "people", "display_name": "People", "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    target_row = (
        await client.post(f"/api/lists/{target}/items/", json={"data": {}})
    ).json()["uuid"]

    link = await add_column(
        list_uuid, name="owner", display_name="Owner", type="lookup",
        config={"target_list_uuid": target},
    )
    row = (
        await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {link: target_row}})
    ).json()
    assert row["data"][link] == target_row

    # Delete the target row: the reference *survives*, because a soft delete has to be
    # undoable and destroying the pointer is not. It is cleared when the row is purged
    # (see tests/test_soft_delete_is_reversible.py).
    assert (await client.delete(f"/api/lists/{target}/items/{target_row}")).status_code == 204
    refreshed = (await client.get(f"/api/lists/{list_uuid}/items/{row['uuid']}")).json()
    assert refreshed["data"][link] == target_row


async def test_blank_means_empty_for_every_column_type(client, list_uuid, add_column):
    """Decided once, centrally: a whitespace-only value clears the cell whatever the
    column is. Per-type handling had "" clear a reference cell but 400 a link cell,
    so the same payload meant different things depending on where it landed."""
    cols = {
        "text": await add_column(list_uuid, name="t", display_name="T", type="text"),
        "link": await add_column(list_uuid, name="l", display_name="L", type="link"),
        "date": await add_column(list_uuid, name="d", display_name="D", type="date"),
    }
    created = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={
            "data": {
                cols["text"]: "hello",
                cols["link"]: "https://example.com",
                cols["date"]: "2026-07-01",
            }
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()["uuid"]

    cleared = await client.patch(
        f"/api/lists/{list_uuid}/items/{item}",
        json={"data": {uuid: "   " for uuid in cols.values()}},
    )
    assert cleared.status_code == 200, cleared.text
    assert all(cleared.json()["data"][uuid] is None for uuid in cols.values())


async def test_records_can_be_searched(client, list_uuid, add_column):
    """Server-side, like every other search here: filtering a fetched page in the
    browser would silently fail to find records past it."""
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    note = await add_column(list_uuid, name="note", display_name="Note", type="text")
    for n, t in [("Ann", "likes cats"), ("Bo", "likes dogs"), ("Cy", "no pets")]:
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {name: n, note: t}}
        )

    # Any column matches, case-insensitively.
    found = (await client.get(f"/api/lists/{list_uuid}/items/?q=ann")).json()
    assert [i["data"][name] for i in found] == ["Ann"]
    found = (await client.get(f"/api/lists/{list_uuid}/items/?q=likes")).json()
    assert sorted(i["data"][name] for i in found) == ["Ann", "Bo"]
    assert (await client.get(f"/api/lists/{list_uuid}/items/?q=zebra")).json() == []

    # A LIKE wildcard is matched literally, not as a wildcard.
    assert (await client.get(f"/api/lists/{list_uuid}/items/?q=%25")).json() == []

    # Blank searches are no-ops rather than matching nothing.
    assert len((await client.get(f"/api/lists/{list_uuid}/items/?q=  ")).json()) == 3


async def test_search_matches_lookup_and_user_labels(
    client, session_factory, workspace_uuid, list_uuid, add_column
):
    """A reference cell stores a uuid, but what a person types is the label. Search
    resolves the label — the linked row's, the account's — and matches on that."""
    from src.auth.users.schemas import UserCreate
    from src.auth.users.services import UserService

    async with session_factory() as db:
        alice = await UserService.create(
            db, UserCreate(username="alice", email="alice@example.com", password="pw")
        )
        alice_uuid = str(alice.uuid)

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
    school_name = await add_column(
        target, name="name", display_name="Name", type="text"
    )
    springfield = (
        await client.post(
            f"/api/lists/{target}/items/",
            json={"data": {school_name: "Springfield High"}},
        )
    ).json()["uuid"]
    shelbyville = (
        await client.post(
            f"/api/lists/{target}/items/",
            json={"data": {school_name: "Shelbyville High"}},
        )
    ).json()["uuid"]

    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    school = await add_column(
        list_uuid,
        name="school",
        display_name="School",
        type="lookup",
        config={"target_list_uuid": target},
    )
    owner = await add_column(list_uuid, name="owner", display_name="Owner", type="user")
    reviewers = await add_column(
        list_uuid,
        name="reviewers",
        display_name="Reviewers",
        type="user",
        config={"multiple": True},
    )

    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={
            "data": {
                name: "Ann",
                school: springfield,
                owner: alice_uuid,
                reviewers: [alice_uuid],
            }
        },
    )
    await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {name: "Bo", school: shelbyville}},
    )

    async def search(q):
        found = (await client.get(f"/api/lists/{list_uuid}/items/?q={q}")).json()
        return sorted(i["data"][name] for i in found)

    # The lookup's label, not its uuid.
    assert await search("springfield") == ["Ann"]
    assert await search("high") == ["Ann", "Bo"]  # both schools match
    # A username, on a single-valued and a multi-valued cell alike.
    assert await search("alice") == ["Ann"]
    # And plain text still works.
    assert await search("Bo") == ["Bo"]


@pytest_asyncio.fixture
async def paged_list(client, list_uuid, add_column):
    """Ten records: `n` runs "00".."09" in creation order, `grp` alternates."""
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    grp = await add_column(list_uuid, name="grp", display_name="Grp", type="text")
    for i in range(10):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {col: f"{i:02d}", grp: "alpha" if i % 2 == 0 else "beta"}},
        )
        assert resp.status_code == 201, resp.text
    return list_uuid, col, grp


async def test_after_walks_the_whole_list_exactly_once(client, paged_list):
    list_uuid, col, _ = paged_list
    seen, cursor = [], None
    while True:
        query = "limit=3" + (f"&after={cursor}" if cursor else "")
        resp = await client.get(f"/api/lists/{list_uuid}/items/?{query}")
        assert resp.status_code == 200, resp.text
        page = resp.json()
        if not page:
            break
        seen.extend(r["data"][col] for r in page)
        cursor = page[-1]["uuid"]

    # Every record, once, in the order `skip` would have produced.
    assert seen == [f"{i:02d}" for i in range(10)]


async def test_after_and_skip_agree_page_for_page(client, paged_list):
    list_uuid, _, _ = paged_list
    first = (await client.get(f"/api/lists/{list_uuid}/items/?limit=4")).json()
    by_skip = (await client.get(f"/api/lists/{list_uuid}/items/?skip=4&limit=4")).json()
    by_after = (
        await client.get(
            f"/api/lists/{list_uuid}/items/?after={first[-1]['uuid']}&limit=4"
        )
    ).json()
    assert [r["uuid"] for r in by_after] == [r["uuid"] for r in by_skip]


async def test_after_still_reports_the_total(client, paged_list):
    list_uuid, _, _ = paged_list
    first = (await client.get(f"/api/lists/{list_uuid}/items/?limit=3")).json()
    resp = await client.get(
        f"/api/lists/{list_uuid}/items/?after={first[-1]['uuid']}&limit=3"
    )
    # The count is of the whole matching set, not of what is left after the cursor.
    assert resp.headers["X-Total-Count"] == "10"


async def test_a_delete_behind_the_cursor_does_not_skip_a_record(client, paged_list):
    """The correctness half of keyset paging, and why it is not only the faster one.

    `skip` names a position, so anything removed ahead of that position pulls every
    later record back one place and the next page steps straight over one the caller
    never saw. Ids only ever grow, so an *insert* cannot do this — a delete can, and
    a bin that anyone with `delete` can fill makes it an ordinary occurrence rather
    than a race. A cursor names a record, so nothing happening elsewhere moves it.
    """
    list_uuid, col, _ = paged_list
    first = (await client.get(f"/api/lists/{list_uuid}/items/?limit=3")).json()
    cursor = first[-1]["uuid"]
    assert [r["data"][col] for r in first] == ["00", "01", "02"]

    await client.delete(f"/api/lists/{list_uuid}/items/{first[0]['uuid']}")

    by_skip = (
        await client.get(f"/api/lists/{list_uuid}/items/?skip=3&limit=3")
    ).json()
    by_after = (
        await client.get(f"/api/lists/{list_uuid}/items/?after={cursor}&limit=3")
    ).json()

    # "03" has moved into the slot the offset already passed, so `skip` never shows it.
    assert [r["data"][col] for r in by_skip] == ["04", "05", "06"]
    assert [r["data"][col] for r in by_after] == ["03", "04", "05"]


async def test_after_respects_a_search(client, paged_list):
    list_uuid, col, _ = paged_list
    alpha = f"/api/lists/{list_uuid}/items/?q=alpha&limit=2"
    page = (await client.get(alpha)).json()
    assert [r["data"][col] for r in page] == ["00", "02"]
    nxt = (await client.get(f"{alpha}&after={page[-1]['uuid']}")).json()
    # The cursor moves through the *matching* records, not the list's own order.
    assert [r["data"][col] for r in nxt] == ["04", "06"]


async def test_after_is_refused_when_the_listing_is_sorted(client, paged_list):
    list_uuid, col, _ = paged_list
    first = (await client.get(f"/api/lists/{list_uuid}/items/?limit=3")).json()
    cursor = first[-1]["uuid"]

    resp = await client.get(
        f"/api/lists/{list_uuid}/items/?after={cursor}&sort_by={col}"
    )
    assert resp.status_code == 400, resp.text
    assert "sorted or grouped" in resp.json()["detail"]

    resp = await client.get(
        f"/api/lists/{list_uuid}/items/?after={cursor}&group_by={col}"
    )
    assert resp.status_code == 400, resp.text

    # `sort_by=` is "no order at all", which is the default order, so it is fine.
    resp = await client.get(f"/api/lists/{list_uuid}/items/?after={cursor}&sort_by=")
    assert resp.status_code == 200, resp.text


async def test_after_is_refused_alongside_skip(client, paged_list):
    list_uuid, _, _ = paged_list
    first = (await client.get(f"/api/lists/{list_uuid}/items/?limit=3")).json()
    resp = await client.get(
        f"/api/lists/{list_uuid}/items/?after={first[-1]['uuid']}&skip=3"
    )
    assert resp.status_code == 400, resp.text
    assert "not both" in resp.json()["detail"]


async def test_after_is_refused_for_a_uuid_this_list_does_not_have(
    client, paged_list, workspace_uuid
):
    """Unknown, binned, and belonging to another list are all the same refusal.

    Each of them would otherwise silently restart the walk from the top, which reads
    as the list having rewound rather than as the caller's mistake.
    """
    list_uuid, _col, _grp = paged_list
    base = f"/api/lists/{list_uuid}/items/?after="

    unknown = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(base + unknown)).status_code == 400

    rows = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    binned = rows[0]["uuid"]
    await client.delete(f"/api/lists/{list_uuid}/items/{binned}")
    resp = await client.get(base + binned)
    assert resp.status_code == 400, resp.text
    assert "no page after it" in resp.json()["detail"]

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
    elsewhere = (
        await client.post(f"/api/lists/{other}/items/", json={"data": {}})
    ).json()["uuid"]
    assert (await client.get(base + elsewhere)).status_code == 400


async def test_count_agrees_with_the_total_the_listing_reports(client, paged_list):
    list_uuid, _, _ = paged_list
    for query in ("", "?q=alpha", "?filters=%7B%22conditions%22%3A%5B%5D%7D"):
        listing = await client.get(f"/api/lists/{list_uuid}/items/{query}")
        counted = await client.get(f"/api/lists/{list_uuid}/items/count{query}")
        assert counted.status_code == 200, counted.text
        # The two are the same number by construction — both are built off
        # `visible_stmt` — and this is what keeps them that way.
        assert str(counted.json()["total"]) == listing.headers["X-Total-Count"], query


async def test_count_ignores_the_page_and_the_order(client, paged_list):
    list_uuid, col, _ = paged_list
    plain = (await client.get(f"/api/lists/{list_uuid}/items/count")).json()["total"]
    assert plain == 10
    # None of these change how many records there are, and the endpoint does not
    # accept them; passing them anyway must not change the answer either.
    noisy = (
        await client.get(
            f"/api/lists/{list_uuid}/items/count"
            f"?skip=5&limit=2&sort_by={col}&sort_dir=desc&group_by={col}"
        )
    ).json()["total"]
    assert noisy == plain


async def test_count_is_not_parsed_as_a_record_uuid(client, paged_list):
    """`/count` is declared before `/{uuid}`; this is what holds that ordering."""
    list_uuid, _, _ = paged_list
    resp = await client.get(f"/api/lists/{list_uuid}/items/count")
    assert resp.status_code == 200, resp.text
    assert "total" in resp.json()


async def test_total_false_omits_the_header_and_the_rows_are_unchanged(
    client, paged_list
):
    list_uuid, col, _ = paged_list
    withhdr = await client.get(f"/api/lists/{list_uuid}/items/?limit=3")
    without = await client.get(f"/api/lists/{list_uuid}/items/?limit=3&total=false")

    assert withhdr.headers["X-Total-Count"] == "10"
    assert "X-Total-Count" not in without.headers
    # Suppressing the total changes nothing about which records come back.
    assert [r["data"][col] for r in without.json()] == [
        r["data"][col] for r in withhdr.json()
    ]


async def test_count_drops_a_binned_record(client, paged_list):
    """The count is the paginator's total, so it must not describe more records
    than the listing will hand over — a total reaching past them is how a paginator
    offers pages that come back empty."""
    list_uuid, _, _ = paged_list
    rows = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    await client.delete(f"/api/lists/{list_uuid}/items/{rows[0]['uuid']}")

    counted = (await client.get(f"/api/lists/{list_uuid}/items/count")).json()["total"]
    listed = await client.get(f"/api/lists/{list_uuid}/items/")
    assert counted == 9
    assert counted == len(listed.json()) == int(listed.headers["X-Total-Count"])


async def test_deleted_records_are_listed_and_restorable(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "keep me"}}
    )
    uuid = created.json()["uuid"]

    assert (await client.delete(f"/api/lists/{list_uuid}/items/{uuid}")).status_code == 204
    # Gone from the live listing...
    assert (await client.get(f"/api/lists/{list_uuid}/items/")).json() == []

    # ...but in the bin, with its deletion time and a total-count header.
    binned = await client.get(f"/api/lists/{list_uuid}/items/deleted")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    rows = binned.json()
    assert [r["uuid"] for r in rows] == [uuid]
    assert rows[0]["deleted_at"] is not None
    assert rows[0]["data"][col] == "keep me"

    # Restore brings it back to the live listing and empties the bin.
    restored = await client.post(f"/api/lists/{list_uuid}/items/{uuid}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"][col] == "keep me"
    assert [i["uuid"] for i in (await client.get(f"/api/lists/{list_uuid}/items/")).json()] == [uuid]
    assert (await client.get(f"/api/lists/{list_uuid}/items/deleted")).json() == []


async def test_restore_rejected_when_it_would_duplicate_a_unique_value(
    client, list_uuid, add_column
):
    col = await add_column(
        list_uuid, name="code", display_name="Code", type="text",
        config={"unique": True},
    )

    async def add(value):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {col: value}}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["uuid"]

    first = await add("X")
    # Bin the first, then let a new record take the value it held.
    assert (await client.delete(f"/api/lists/{list_uuid}/items/{first}")).status_code == 204
    await add("X")

    # Restoring the first would now collide on the unique column.
    resp = await client.post(f"/api/lists/{list_uuid}/items/{first}/restore")
    assert resp.status_code == 409, resp.text
    assert "unique" in resp.json()["detail"].lower()


async def test_restore_of_a_live_record_is_a_404(client, list_uuid, add_column):
    col = await add_column(list_uuid, name="n", display_name="N", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: "a"}}
    )
    uuid = created.json()["uuid"]
    # It is not in the bin, so there is nothing to restore.
    assert (await client.post(f"/api/lists/{list_uuid}/items/{uuid}/restore")).status_code == 404
