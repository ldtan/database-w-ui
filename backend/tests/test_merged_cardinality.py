"""`allow_shared`: whether several rows may point at one linked record.

A merged form pairs a row of this list with a record of another. `allow_shared: false`
— the default — means that pairing is 1:1: a linked record may be reached by at most
one row. Turning it on permits 1:many, which is what makes editing the linked record
change what other rows show, and what makes the mirror refresh touch more than one row.

**The invariant is enforced where uniqueness is enforced, not where the form is.** A
form is a layout; it cannot be the authority on what the data may say. So these tests
break the rule through three different doors — the items route, a single-cell PATCH,
and a CSV import — because a check that only fires on the merged route is a check that
does not hold.
"""
import io

import pytest


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
    await client.patch(f"/api/lists/{lst}", json={"primary_column_uuid": name_col})
    rows = {}
    for who in ("Ana", "Ben"):
        rows[who] = (
            await client.post(
                f"/api/lists/{lst}/items/", json={"data": {name_col: who}}
            )
        ).json()["uuid"]
    return lst, name_col, rows


async def _merged(client, list_uuid, link, *, allow_shared, borrowed=None):
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
                    "fields": (
                        []
                        if borrowed is None
                        else [
                            {
                                "column_uuid": borrowed,
                                "included": True,
                                "enabled": True,
                                "order": 0,
                            }
                        ]
                    ),
                    "allow_shared": allow_shared,
                },
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


@pytest.fixture
def scenario(client, workspace_uuid, list_uuid, add_column):
    async def _build(*, allow_shared):
        people, name_col, rows = await _people(client, workspace_uuid, add_column)
        link = await add_column(
            list_uuid,
            name="person",
            display_name="Person",
            type="lookup",
            config={"target_list_uuid": people},
        )
        await _merged(client, list_uuid, link, allow_shared=allow_shared)
        return people, name_col, rows, link

    return _build


async def test_a_second_row_cannot_claim_the_same_linked_record(
    client, list_uuid, scenario
):
    _, _, rows, link = await scenario(allow_shared=False)

    first = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ana"]}}
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ana"]}}
    )
    assert second.status_code == 409, second.text
    assert "already linked" in second.text
    # The refusal names the way out rather than just saying no.
    assert "shared linking" in second.text


async def test_a_different_linked_record_is_fine(client, list_uuid, scenario):
    _, _, rows, link = await scenario(allow_shared=False)

    for who in ("Ana", "Ben"):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {link: rows[who]}}
        )
        assert resp.status_code == 201, resp.text


async def test_an_inline_cell_edit_cannot_break_it_either(
    client, list_uuid, scenario
):
    """The door that matters most. Editing the lookup cell in the grid never touches
    the merged form, so a check living on the merged route would not see this at all."""
    _, _, rows, link = await scenario(allow_shared=False)

    await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ana"]}})
    second = (
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ben"]}}
        )
    ).json()["uuid"]

    moved = await client.patch(
        f"/api/lists/{list_uuid}/items/{second}", json={"data": {link: rows["Ana"]}}
    )
    assert moved.status_code == 409, moved.text
    assert "already linked" in moved.text


async def test_a_row_can_be_saved_without_moving_its_own_link(
    client, list_uuid, scenario
):
    """`exclude_id` doing its job: a record must not conflict with itself. Re-sending
    the value it already holds is a keep, not a claim."""
    _, _, rows, link = await scenario(allow_shared=False)

    made = (
        await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ana"]}}
        )
    ).json()["uuid"]

    again = await client.patch(
        f"/api/lists/{list_uuid}/items/{made}", json={"data": {link: rows["Ana"]}}
    )
    assert again.status_code == 200, again.text


async def test_an_import_cannot_break_it(
    client, workspace_uuid, list_uuid, add_column
):
    """The third door. Import goes through the same validation, so it inherits this
    without knowing merged forms exist."""
    people, name_col, rows = await _people(client, workspace_uuid, add_column)
    link = await add_column(
        list_uuid,
        name="person",
        display_name="Person",
        type="lookup",
        config={"target_list_uuid": people},
    )
    await _merged(client, list_uuid, link, allow_shared=False)

    # Two rows naming the same linked record by label — what a spreadsheet holds.
    body = "person\nAna\nAna\n"
    resp = await client.post(
        f"/api/lists/{list_uuid}/import",
        files={"file": ("rows.csv", io.BytesIO(body.encode()), "text/csv")},
    )
    assert resp.status_code in (200, 400, 409), resp.text
    # However the importer reports it, the list must not end up with two rows on one
    # linked record — that is the invariant, not the status code.
    listed = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    linked = [row["data"].get(link) for row in listed]
    assert linked.count(rows["Ana"]) <= 1


async def test_allow_shared_permits_what_the_default_refuses(
    client, list_uuid, scenario
):
    _, _, rows, link = await scenario(allow_shared=True)

    for _ in range(3):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ana"]}}
        )
        assert resp.status_code == 201, resp.text

    listed = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [row["data"][link] for row in listed] == [rows["Ana"]] * 3


async def test_turning_it_off_leaves_existing_shared_links_readable(
    client, list_uuid, workspace_uuid, add_column
):
    """The `unique` precedent (known-gaps §5): turning the rule on leaves existing
    duplicates alone and they surface on the next save of one of them. There is no
    sweep, for the same reason there is no cascade-rename."""
    people, _, rows = await _people(client, workspace_uuid, add_column)
    link = await add_column(
        list_uuid,
        name="person",
        display_name="Person",
        type="lookup",
        config={"target_list_uuid": people},
    )
    form_uuid = await _merged(client, list_uuid, link, allow_shared=True)

    made = []
    for _ in range(2):
        made.append(
            (
                await client.post(
                    f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ana"]}}
                )
            ).json()["uuid"]
        )

    tightened = await client.patch(
        f"/api/lists/{list_uuid}/forms/{form_uuid}",
        json={
            "config": {
                "fields": [],
                "merge": {
                    "link_column_uuid": link,
                    "fields": [],
                    "allow_shared": False,
                },
            }
        },
    )
    assert tightened.status_code == 200, tightened.text

    # Both rows are still there and still readable.
    listed = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert [row["data"][link] for row in listed] == [rows["Ana"]] * 2

    # But saving one of them now has to answer for it.
    resaved = await client.patch(
        f"/api/lists/{list_uuid}/items/{made[1]}", json={"data": {link: rows["Ana"]}}
    )
    assert resaved.status_code == 409, resaved.text


async def test_a_list_with_no_merged_form_is_unaffected(
    client, workspace_uuid, list_uuid, add_column
):
    """The guard: an ordinary lookup column stays many-to-one, as it always was."""
    people, _, rows = await _people(client, workspace_uuid, add_column)
    link = await add_column(
        list_uuid,
        name="person",
        display_name="Person",
        type="lookup",
        config={"target_list_uuid": people},
    )

    for _ in range(3):
        resp = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {link: rows["Ana"]}}
        )
        assert resp.status_code == 201, resp.text
