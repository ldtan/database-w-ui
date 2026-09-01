"""`multiple` may only be set on a type that can hold more than one value.

It used to be accepted on all eighteen, and the result was not a column that
ignored the flag — it was a column that could never hold anything at all.
`coerce_shape` wraps an incoming scalar in a one-element list because the config
says the column is multiple, and `validate_scalar` then measures that list against
the *scalar* rule for the type and refuses it: `Column 'tags' expects a string`.
Sending a list fails identically, so there is no value that works. The 201 came
back clean and every subsequent write of that cell was a 400, with no way out that
the UI offers.

Only `choice` and the three REFERENCE_TYPES have anywhere to put a second value —
`choice` through the list branch in `validate_scalar`, the references through
`_validate_value` resolving a set. That set is `MULTIPLE_TYPES`, and it is closed.

Refused rather than implemented, for the same reason `unique` + `multiple` is (see
test_unique_multiple.py): making it work means deciding what `min`/`max`, a
`default`, a formula reference and every filter operator mean element-wise, which
is the separate piece of work known-gaps names. A column must not claim something
it does not do.
"""
import pytest

# Every type that stores a value of its own and is not in MULTIPLE_TYPES. `calculated`
# is left out because it is computed rather than written, and takes no default either.
UNSUPPORTED = [
    "text",
    "number",
    "checkbox",
    "link",
    "email",
    "phone",
    "location",
    "color",
    "rating",
    "duration",
    "date",
    "time",
    "datetime",
]


async def _create(client, list_uuid, **body):
    return await client.post(f"/api/lists/{list_uuid}/columns/", json=body)


@pytest.mark.parametrize("type_", UNSUPPORTED)
async def test_multiple_is_refused_on_a_type_that_cannot_hold_it(
    client, list_uuid, type_
):
    resp = await _create(
        client,
        list_uuid,
        name=f"c_{type_}",
        display_name=type_.title(),
        type=type_,
        config={"multiple": True},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "multiple" in detail.lower()
    # Names what may, so the message is a way forward rather than a refusal.
    assert "choice" in detail


@pytest.mark.parametrize("type_", ["choice", "lookup", "user", "group"])
async def test_multiple_is_allowed_where_it_is_implemented(
    client, workspace_uuid, list_uuid, type_
):
    config: dict = {"multiple": True}
    if type_ == "choice":
        config["choices"] = "a;b;c"
    elif type_ == "lookup":
        target = (
            await client.post(
                "/api/lists/",
                json={
                    "name": "targets",
                    "display_name": "Targets",
                    "workspace_uuid": workspace_uuid,
                },
            )
        ).json()["uuid"]
        config["target_list_uuid"] = target

    resp = await _create(
        client,
        list_uuid,
        name=f"m_{type_}",
        display_name=type_.title(),
        type=type_,
        config=config,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["config"]["multiple"] is True


async def test_a_patch_cannot_turn_it_on_either(client, list_uuid):
    """The check sits after the config merge, so an update is caught like a create.

    This is the shape that would have slipped through a create-only check: make an
    ordinary text column, then PATCH the flag onto it.
    """
    created = await _create(
        client, list_uuid, name="notes", display_name="Notes", type="text", config={}
    )
    assert created.status_code == 201, created.text
    uuid = created.json()["uuid"]

    resp = await client.patch(
        f"/api/lists/{list_uuid}/columns/{uuid}", json={"config": {"multiple": True}}
    )
    assert resp.status_code == 400, resp.text


async def test_a_supported_column_still_takes_a_list(client, list_uuid):
    """The guard refuses a type, not the feature: what worked before still works."""
    created = await _create(
        client,
        list_uuid,
        name="tags",
        display_name="Tags",
        type="choice",
        config={"choices": "a;b;c", "multiple": True},
    )
    uuid = created.json()["uuid"]
    written = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {uuid: ["a", "c"]}}
    )
    assert written.status_code == 201, written.text
    assert written.json()["data"][uuid] == ["a", "c"]
