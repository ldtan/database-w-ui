"""Configuring a merged form: what a `merge` block may say, and what it may not.

A merged form lays a second list's fields beside this list's own, reached through one
`lookup` column. The block stores only that column; which list it means comes from the
column's own `target_list_uuid` on every read, so retargeting the lookup can never
leave a stale pointer here.

The rule worth reading twice is the permission one. `Action.lookup` exists so a
reference list can resolve other people's row labels while staying shut — it discloses
the display column and nothing else. A merged form puts *arbitrary* columns of the
target on screen, so it demands `view`. Accepting `lookup` would turn the narrowest
action in the model into full read access to somebody else's list, which is
`test_a_lookup_only_grant_cannot_build_a_merged_form`.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine, db_module.SessionLocal = engine, session_factory
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def _user(session_factory, name):
    async with session_factory() as db:
        created = await UserService.create(
            db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
        )
        return created, str(created.uuid)


async def _grant(client, resource_uuid, user_uuid, actions, resource_type="list"):
    resp = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": resource_type,
            "resource_uuid": resource_uuid,
            "user_uuid": user_uuid,
            "actions": actions,
        },
    )
    assert resp.status_code == 201, resp.text


async def _target_list(client, workspace_uuid, add_column, name="people"):
    """A list with a text column and a calculated one, to borrow from."""
    lst = (
        await client.post(
            "/api/lists/",
            json={
                "name": name,
                "display_name": name.title(),
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    text_col = await add_column(lst, name="fullname", display_name="Name", type="text")
    num_col = await add_column(lst, name="age", display_name="Age", type="number")
    calc_col = await add_column(
        lst,
        name="doubled",
        display_name="Doubled",
        type="calculated",
        config={"formula": "age * 2"},
    )
    return lst, text_col, num_col, calc_col


async def _link(client, add_column, host, target, **config):
    return await add_column(
        host,
        name=config.pop("name", "person"),
        display_name="Person",
        type="lookup",
        config={"target_list_uuid": target, **config},
    )


async def _form(client, list_uuid, merge, name="merged"):
    """Create a merged form, returning the raw response so refusals can be read."""
    return await client.post(
        f"/api/lists/{list_uuid}/forms/",
        json={
            "name": name,
            "display_name": "Merged",
            "type": "create",
            "config": {"fields": [], "merge": merge},
        },
    )


async def test_a_valid_merge_block_is_accepted(
    client, workspace_uuid, list_uuid, add_column
):
    target, text_col, _, _ = await _target_list(client, workspace_uuid, add_column)
    link = await _link(client, add_column, list_uuid, target)

    resp = await _form(
        client,
        list_uuid,
        {
            "link_column_uuid": link,
            "fields": [
                {"column_uuid": text_col, "included": True, "enabled": True, "order": 0}
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    merge = resp.json()["config"]["merge"]
    assert merge["link_column_uuid"] == link
    assert [f["column_uuid"] for f in merge["fields"]] == [text_col]
    # The cardinality default is the safe one: 1:1 until somebody says otherwise.
    assert merge["allow_shared"] is False


async def test_an_empty_fields_list_means_none_rather_than_all(
    client, workspace_uuid, list_uuid, add_column
):
    """The inverse of `FormConfig.fields`, deliberately. The other list is not this
    form's to enumerate — "all of them" would silently widen as the target gains
    columns, putting fields on screen nobody chose."""
    target, _, _, _ = await _target_list(client, workspace_uuid, add_column)
    link = await _link(client, add_column, list_uuid, target)

    resp = await _form(client, list_uuid, {"link_column_uuid": link, "fields": []})
    assert resp.status_code == 201, resp.text
    assert resp.json()["config"]["merge"]["fields"] == []


async def test_the_link_must_be_a_column_of_this_list(
    client, workspace_uuid, list_uuid, add_column
):
    target, text_col, _, _ = await _target_list(client, workspace_uuid, add_column)

    resp = await _form(client, list_uuid, {"link_column_uuid": text_col, "fields": []})
    assert resp.status_code == 400, resp.text
    assert "column of this list" in resp.text


async def test_the_link_must_be_a_lookup(client, workspace_uuid, list_uuid, add_column):
    plain = await add_column(list_uuid, name="note", display_name="Note", type="text")

    resp = await _form(client, list_uuid, {"link_column_uuid": plain, "fields": []})
    assert resp.status_code == 400, resp.text
    assert "is not a lookup" in resp.text


async def test_a_multiple_link_is_refused(
    client, workspace_uuid, list_uuid, add_column
):
    """One merged row pairs with one linked record. A multi-valued link has no single
    record to write the borrowed fields into."""
    target, _, _, _ = await _target_list(client, workspace_uuid, add_column)
    link = await _link(client, add_column, list_uuid, target, multiple=True)

    resp = await _form(client, list_uuid, {"link_column_uuid": link, "fields": []})
    assert resp.status_code == 400, resp.text
    assert "several values" in resp.text


async def test_a_nested_link_is_refused(client, workspace_uuid, list_uuid, add_column):
    """A nested lookup's options depend on a sibling cell of the same record, which a
    merged create has not written when it needs the target."""
    countries, country_name, _, _ = await _target_list(
        client, workspace_uuid, add_column, name="countries"
    )
    cities = (
        await client.post(
            "/api/lists/",
            json={
                "name": "cities",
                "display_name": "Cities",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    city_country = await _link(
        client, add_column, cities, countries, name="country"
    )

    parent = await _link(
        client, add_column, list_uuid, countries, name="country"
    )
    nested = await _link(
        client,
        add_column,
        list_uuid,
        cities,
        name="city",
        parent_column_uuid=parent,
        target_filter_column_uuid=city_country,
    )

    resp = await _form(client, list_uuid, {"link_column_uuid": nested, "fields": []})
    assert resp.status_code == 400, resp.text
    assert "nested under another column" in resp.text


async def test_a_borrowed_column_must_be_on_the_linked_list(
    client, workspace_uuid, list_uuid, add_column
):
    target, _, _, _ = await _target_list(client, workspace_uuid, add_column)
    link = await _link(client, add_column, list_uuid, target)
    stranger = await add_column(
        list_uuid, name="mine", display_name="Mine", type="text"
    )

    resp = await _form(
        client,
        list_uuid,
        {
            "link_column_uuid": link,
            "fields": [
                {"column_uuid": stranger, "included": True, "enabled": True, "order": 0}
            ],
        },
    )
    assert resp.status_code == 400, resp.text
    assert "not on the linked list" in resp.text


async def test_a_calculated_column_cannot_be_borrowed(
    client, workspace_uuid, list_uuid, add_column
):
    """Its formula is its only writer, so it can never be an input — the rule
    create/update forms already follow for their own list's columns."""
    target, _, _, calc_col = await _target_list(client, workspace_uuid, add_column)
    link = await _link(client, add_column, list_uuid, target)

    resp = await _form(
        client,
        list_uuid,
        {
            "link_column_uuid": link,
            "fields": [
                {"column_uuid": calc_col, "included": True, "enabled": True, "order": 0}
            ],
        },
    )
    assert resp.status_code == 400, resp.text
    assert "calculated column" in resp.text


async def test_a_lookup_only_grant_cannot_build_a_merged_form(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """The security rule, stated as a test.

    `lookup` is enough to *point a column* at the target and resolve its row labels.
    It is deliberately not enough to lay the target's other columns onto a form, which
    would read arbitrary data out of a list its owner shared as a label source only.
    Refused indistinguishably from a missing list, so it cannot be used to probe.
    """
    target, text_col, _, _ = await _target_list(client, workspace_uuid, add_column)
    other, other_uuid = await _user(session_factory, "peeker")
    await _grant(client, list_uuid, other_uuid, ["configure"])
    await _grant(client, target, other_uuid, ["lookup"])

    async with make_client(other) as peeker:
        made = await peeker.post(
            f"/api/lists/{list_uuid}/columns/",
            json={
                "name": "person",
                "display_name": "Person",
                "type": "lookup",
                "config": {"target_list_uuid": target},
            },
        )
        # `lookup` is enough for the column itself — that part is unchanged.
        assert made.status_code == 201, made.text
        link = made.json()["uuid"]

        refused = await _form(
            peeker,
            list_uuid,
            {
                "link_column_uuid": link,
                "fields": [
                    {
                        "column_uuid": text_col,
                        "included": True,
                        "enabled": True,
                        "order": 0,
                    }
                ],
            },
        )
        assert refused.status_code == 400, refused.text
        assert "missing target list" in refused.text


async def test_a_view_grant_can_build_a_merged_form(
    client, make_client, session_factory, workspace_uuid, list_uuid, add_column
):
    """The other half of the rule above: `view` on the target is exactly what it takes,
    without needing to own the list or share a workspace with it."""
    target, text_col, _, _ = await _target_list(client, workspace_uuid, add_column)
    other, other_uuid = await _user(session_factory, "reader")
    await _grant(client, list_uuid, other_uuid, ["configure"])
    await _grant(client, target, other_uuid, ["view"])

    async with make_client(other) as reader:
        link = (
            await reader.post(
                f"/api/lists/{list_uuid}/columns/",
                json={
                    "name": "person",
                    "display_name": "Person",
                    "type": "lookup",
                    "config": {"target_list_uuid": target},
                },
            )
        ).json()["uuid"]

        resp = await _form(
            reader,
            list_uuid,
            {
                "link_column_uuid": link,
                "fields": [
                    {
                        "column_uuid": text_col,
                        "included": True,
                        "enabled": True,
                        "order": 0,
                    }
                ],
            },
        )
        assert resp.status_code == 201, resp.text


async def test_retargeting_the_link_clears_the_borrowed_fields(
    client, workspace_uuid, list_uuid, add_column
):
    """The borrowed uuids name columns of the list the link used to reach. After a
    retarget they name columns of a list this form no longer sees, so keeping them
    would render blank fields with nothing to explain why."""
    target, text_col, _, _ = await _target_list(client, workspace_uuid, add_column)
    elsewhere, _, _, _ = await _target_list(
        client, workspace_uuid, add_column, name="places"
    )
    link = await _link(client, add_column, list_uuid, target)
    form_uuid = (
        await _form(
            client,
            list_uuid,
            {
                "link_column_uuid": link,
                "fields": [
                    {
                        "column_uuid": text_col,
                        "included": True,
                        "enabled": True,
                        "order": 0,
                    }
                ],
            },
        )
    ).json()["uuid"]

    retarget = await client.patch(
        f"/api/lists/{list_uuid}/columns/{link}",
        json={"config": {"target_list_uuid": elsewhere}},
    )
    assert retarget.status_code == 200, retarget.text

    after = (await client.get(f"/api/lists/{list_uuid}/forms/{form_uuid}")).json()
    # The link survives — it is still a merged form — but it borrows nothing yet.
    assert after["config"]["merge"]["link_column_uuid"] == link
    assert after["config"]["merge"]["fields"] == []


async def test_deleting_the_link_leaves_the_merge_block_alone(
    client, workspace_uuid, list_uuid, add_column
):
    """Unlike a retarget. A deleted column can be restored, and the merge has to come
    back with it — so nothing is cleared and the block simply reads as inert."""
    target, text_col, _, _ = await _target_list(client, workspace_uuid, add_column)
    link = await _link(client, add_column, list_uuid, target)
    form_uuid = (
        await _form(
            client,
            list_uuid,
            {
                "link_column_uuid": link,
                "fields": [
                    {
                        "column_uuid": text_col,
                        "included": True,
                        "enabled": True,
                        "order": 0,
                    }
                ],
            },
        )
    ).json()["uuid"]

    deleted = await client.delete(f"/api/lists/{list_uuid}/columns/{link}")
    assert deleted.status_code == 204, deleted.text

    after = (await client.get(f"/api/lists/{list_uuid}/forms/{form_uuid}")).json()
    assert after["config"]["merge"]["link_column_uuid"] == link
    assert [f["column_uuid"] for f in after["config"]["merge"]["fields"]] == [text_col]

    restored = await client.post(f"/api/lists/{list_uuid}/columns/{link}/restore")
    assert restored.status_code == 200, restored.text
    back = (await client.get(f"/api/lists/{list_uuid}/forms/{form_uuid}")).json()
    assert [f["column_uuid"] for f in back["config"]["merge"]["fields"]] == [text_col]


async def test_an_unchanged_merge_block_is_not_revalidated(
    client, workspace_uuid, list_uuid, add_column
):
    """A form whose link column has been deleted must stay editable. Re-validating a
    block nobody touched would make the delete quietly lock the form."""
    target, text_col, _, _ = await _target_list(client, workspace_uuid, add_column)
    link = await _link(client, add_column, list_uuid, target)
    stored = {
        "link_column_uuid": link,
        "fields": [
            {
                "column_uuid": text_col,
                "included": True,
                "enabled": True,
                "order": 0,
                "label_override": None,
            }
        ],
        "allow_shared": False,
    }
    form_uuid = (await _form(client, list_uuid, stored)).json()["uuid"]
    await client.delete(f"/api/lists/{list_uuid}/columns/{link}")

    # Re-saving the layout, merge block untouched — what "Save layout" sends.
    resp = await client.patch(
        f"/api/lists/{list_uuid}/forms/{form_uuid}",
        json={"config": {"fields": [], "merge": stored}},
    )
    assert resp.status_code == 200, resp.text
