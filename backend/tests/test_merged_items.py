"""Writing both halves of a merged form.

One submit, two records, one transaction. The test this file exists for is
`test_a_failure_on_this_side_leaves_no_record_on_the_other`: a merged create that
refuses must leave nothing behind, because a half-written pair is a record in another
list that nothing points at — invisible in every UI and findable only by counting.
"""
import pytest
from sqlalchemy import func, select

from src.lists.items.models import ListItem


async def _people(client, workspace_uuid, add_column):
    lst = (
        await client.post(
            "/api/lists/",
            json={
                "name": "people",
                "display_name": "People",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    name_col = await add_column(lst, name="fullname", display_name="Name", type="text")
    age_col = await add_column(lst, name="age", display_name="Age", type="number")
    await client.patch(f"/api/lists/{lst}", json={"primary_column_uuid": name_col})
    return lst, name_col, age_col


async def _merged_form(client, list_uuid, link, borrowed, allow_shared=False):
    resp = await client.post(
        f"/api/lists/{list_uuid}/forms/",
        json={
            "name": "merged",
            "display_name": "Merged",
            "type": "create",
            "config": {
                "fields": [],
                "merge": {
                    "link_column_uuid": link,
                    "fields": [
                        {
                            "column_uuid": col,
                            "included": True,
                            "enabled": True,
                            "order": i,
                        }
                        for i, col in enumerate(borrowed)
                    ],
                    "allow_shared": allow_shared,
                },
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


@pytest.fixture
def scenario(client, workspace_uuid, list_uuid, add_column):
    async def _build(*, allow_shared=False, required_here=False):
        people, name_col, age_col = await _people(client, workspace_uuid, add_column)
        status_col = await add_column(
            list_uuid,
            name="status",
            display_name="Status",
            type="text",
            config={"required": True} if required_here else {},
        )
        link = await add_column(
            list_uuid,
            name="person",
            display_name="Person",
            type="lookup",
            config={"target_list_uuid": people},
        )
        form = await _merged_form(
            client, list_uuid, link, [name_col, age_col], allow_shared
        )
        return {
            "people": people,
            "name": name_col,
            "age": age_col,
            "status": status_col,
            "link": link,
            "form": form,
        }

    return _build


async def _count(session_factory, list_uuid_str=None):
    async with session_factory() as db:
        return await db.scalar(
            select(func.count()).select_from(ListItem).where(
                ListItem.deleted_at.is_(None)
            )
        )


async def test_one_submit_writes_both_records_and_links_them(
    client, list_uuid, scenario
):
    s = await scenario()

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "active"},
            "linked_data": {s["name"]: "Ana", s["age"]: 19},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["item"]["data"][s["status"]] == "active"
    assert body["linked_item"]["data"][s["name"]] == "Ana"
    assert body["linked_item"]["data"][s["age"]] == 19
    # The link cell was written from the record just created, not from the client.
    assert body["item"]["data"][s["link"]] == body["linked_item"]["uuid"]

    # And the linked record really is in the other list.
    people_rows = (await client.get(f"/api/lists/{s['people']}/items/")).json()
    assert [r["data"][s["name"]] for r in people_rows] == ["Ana"]


async def test_an_existing_record_can_be_linked_without_duplicating_it(
    client, list_uuid, scenario
):
    s = await scenario()
    existing = (
        await client.post(
            f"/api/lists/{s['people']}/items/",
            json={"data": {s["name"]: "Ben", s["age"]: 22}},
        )
    ).json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "active"},
            "linked_uuid": existing,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["linked_item"]["uuid"] == existing

    people_rows = (await client.get(f"/api/lists/{s['people']}/items/")).json()
    assert len(people_rows) == 1


async def test_picking_an_existing_record_does_not_rewrite_it_by_default(
    client, list_uuid, scenario
):
    """`update_linked` defaults to false so a *create* here cannot silently change a
    record other rows may already be showing."""
    s = await scenario()
    existing = (
        await client.post(
            f"/api/lists/{s['people']}/items/",
            json={"data": {s["name"]: "Ben", s["age"]: 22}},
        )
    ).json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "active"},
            "linked_uuid": existing,
            "linked_data": {s["name"]: "Benjamin"},
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["linked_item"]["data"][s["name"]] == "Ben"


async def test_update_linked_writes_the_picked_record(client, list_uuid, scenario):
    s = await scenario()
    existing = (
        await client.post(
            f"/api/lists/{s['people']}/items/",
            json={"data": {s["name"]: "Ben", s["age"]: 22}},
        )
    ).json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "active"},
            "linked_uuid": existing,
            "linked_data": {s["name"]: "Benjamin"},
            "update_linked": True,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["linked_item"]["data"][s["name"]] == "Benjamin"


async def test_a_failure_on_this_side_leaves_no_record_on_the_other(
    client, list_uuid, scenario, session_factory
):
    """**The test this whole module exists for.**

    The linked record is staged first, because this side needs its uuid. So every
    ordinary refusal here — a required column left empty, a unique clash — happens
    with a record already inserted on the other side. If the two writes were separate
    transactions that record would survive, pointed at by nothing.
    """
    s = await scenario(required_here=True)
    before = await _count(session_factory)

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            # `status` is required and deliberately absent.
            "data": {},
            "linked_data": {s["name"]: "Ana", s["age"]: 19},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "required" in resp.text

    # Nothing landed anywhere — not in this list, and not in the linked one.
    assert await _count(session_factory) == before
    people_rows = (await client.get(f"/api/lists/{s['people']}/items/")).json()
    assert people_rows == []


async def test_a_cardinality_clash_also_leaves_nothing_behind(
    client, list_uuid, scenario, session_factory
):
    """The other way a merged create refuses: 1:1 already satisfied. Same rollback."""
    s = await scenario()
    first = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "active"},
            "linked_data": {s["name"]: "Ana"},
        },
    )
    assert first.status_code == 201, first.text
    linked = first.json()["linked_item"]["uuid"]
    before = await _count(session_factory)

    clash = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "second"},
            "linked_uuid": linked,
        },
    )
    assert clash.status_code == 409, clash.text
    assert await _count(session_factory) == before


async def test_update_writes_both_sides(client, list_uuid, scenario):
    s = await scenario()
    created = (
        await client.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": s["form"],
                "data": {s["status"]: "active"},
                "linked_data": {s["name"]: "Ana", s["age"]: 19},
            },
        )
    ).json()
    row = created["item"]["uuid"]

    resp = await client.patch(
        f"/api/lists/{list_uuid}/items/{row}/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "dropped"},
            "linked_data": {s["age"]: 20},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item"]["data"][s["status"]] == "dropped"
    assert body["linked_item"]["data"][s["age"]] == 20
    # The untouched borrowed field is merged, not replaced.
    assert body["linked_item"]["data"][s["name"]] == "Ana"


async def test_updating_an_unlinked_row_refuses_rather_than_dropping_the_values(
    client, list_uuid, scenario
):
    s = await scenario()
    row = (
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {s["status"]: "active"}}
        )
    ).json()["uuid"]

    resp = await client.patch(
        f"/api/lists/{list_uuid}/items/{row}/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "dropped"},
            "linked_data": {s["name"]: "Nobody"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "not linked" in resp.text
    # And this side was not written either — the refusal is the whole request.
    after = (await client.get(f"/api/lists/{list_uuid}/items/{row}")).json()
    assert after["data"][s["status"]] == "active"


async def test_deleting_a_merged_record_leaves_the_linked_one_alive(
    client, list_uuid, scenario
):
    """No cascade. An action in this list must not destroy a record in another one."""
    s = await scenario()
    created = (
        await client.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": s["form"],
                "data": {s["status"]: "active"},
                "linked_data": {s["name"]: "Ana"},
            },
        )
    ).json()

    deleted = await client.delete(
        f"/api/lists/{list_uuid}/items/{created['item']['uuid']}"
    )
    assert deleted.status_code == 204, deleted.text

    people_rows = (await client.get(f"/api/lists/{s['people']}/items/")).json()
    assert [r["uuid"] for r in people_rows] == [created["linked_item"]["uuid"]]


async def test_several_rows_may_share_one_record_when_allowed(
    client, list_uuid, scenario
):
    s = await scenario(allow_shared=True)
    first = (
        await client.post(
            f"/api/lists/{list_uuid}/items/merged",
            json={
                "form_uuid": s["form"],
                "data": {s["status"]: "active"},
                "linked_data": {s["name"]: "Ana"},
            },
        )
    ).json()
    linked = first["linked_item"]["uuid"]

    second = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "also"},
            "linked_uuid": linked,
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["linked_item"]["uuid"] == linked
    # Both rows read the same linked values, which is the fan-out the toggle warns of.
    assert second.json()["linked_item"]["data"][s["name"]] == "Ana"


async def test_a_form_with_no_merge_block_is_not_a_merged_route(
    client, list_uuid, scenario
):
    s = await scenario()
    plain = (
        await client.post(
            f"/api/lists/{list_uuid}/forms/",
            json={
                "name": "plain",
                "display_name": "Plain",
                "type": "create",
                "config": {"fields": []},
            },
        )
    ).json()["uuid"]

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={"form_uuid": plain, "data": {}, "linked_data": {}},
    )
    assert resp.status_code == 400, resp.text
    assert "No merged form" in resp.text


async def test_linking_a_record_that_does_not_exist_is_refused(
    client, list_uuid, scenario
):
    s = await scenario()

    resp = await client.post(
        f"/api/lists/{list_uuid}/items/merged",
        json={
            "form_uuid": s["form"],
            "data": {s["status"]: "active"},
            "linked_uuid": "00000000-0000-7000-8000-000000000000",
        },
    )
    assert resp.status_code == 400, resp.text
    assert "No record with that uuid" in resp.text
