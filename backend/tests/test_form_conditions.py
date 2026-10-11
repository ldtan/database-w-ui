"""A form field's `condition` — the block that decides when the field applies.

The server stores and validates it and never acts on it: `TableRecordService._validate_data`
enforces `required` and `unique` whatever a form says, and CSV import, a QR fill and a
direct API write reach no form at all. So what is pinned here is exactly what the server
is responsible for — that the block round-trips intact, and that it cannot be saved
naming a column the form has no business testing.
"""
import pytest_asyncio


@pytest_asyncio.fixture
async def form_uuid(client, table_uuid):
    """The table's auto-provisioned create form."""
    forms = (await client.get(f"/api/tables/{table_uuid}/forms/")).json()
    return next(f for f in forms if f["type"] == "create")["uuid"]


def _config(fields):
    return {"config": {"fields": fields}}


def _field(column_uuid, condition=None, **overrides):
    field = {
        "column_uuid": column_uuid,
        "included": True,
        "enabled": True,
        "order": 0,
        "label_override": None,
    }
    field.update(overrides)
    if condition is not None:
        field["condition"] = condition
    return field


def _when(column_uuid, operator="is", value="Rejected", conjunction="and"):
    return {
        "when": {
            "conjunction": conjunction,
            "conditions": [
                {"column_uuid": column_uuid, "operator": operator, "value": value}
            ],
        },
        "effect": "hide",
    }


async def test_condition_round_trips(client, table_uuid, form_uuid, add_column):
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )

    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config([_field(status), _field(reason, _when(status), order=1)]),
    )
    assert resp.status_code == 200, resp.text

    stored = next(
        f for f in resp.json()["config"]["fields"] if f["column_uuid"] == reason
    )
    assert stored["condition"] == {
        "when": {
            "conjunction": "and",
            "conditions": [
                {"column_uuid": status, "operator": "is", "value": "Rejected"}
            ],
        },
        "effect": "hide",
    }


async def test_a_field_without_a_condition_stays_unconditional(
    client, table_uuid, form_uuid, add_column
):
    """The absent case is the one every existing form is in, so it has to survive.

    `FormConfig` is `extra="forbid"` and the whole config is replaced on write, so a
    key that stopped being optional would 422 every form provisioned before conditions
    existed.
    """
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}", json=_config([_field(status)])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["fields"][0]["condition"] is None


async def test_effect_defaults_to_hide(client, table_uuid, form_uuid, add_column):
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config(
            [
                _field(status),
                _field(
                    reason,
                    {
                        "when": {
                            "conditions": [
                                {"column_uuid": status, "operator": "is", "value": "x"}
                            ]
                        }
                    },
                    order=1,
                ),
            ]
        ),
    )
    assert resp.status_code == 200, resp.text
    stored = next(
        f for f in resp.json()["config"]["fields"] if f["column_uuid"] == reason
    )
    assert stored["condition"]["effect"] == "hide"


async def test_disable_is_the_other_effect(client, table_uuid, form_uuid, add_column):
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )
    condition = _when(status)
    condition["effect"] = "disable"
    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config([_field(status), _field(reason, condition, order=1)]),
    )
    assert resp.status_code == 200, resp.text


async def test_an_unknown_effect_is_refused(client, table_uuid, form_uuid, add_column):
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )
    condition = _when(status)
    condition["effect"] = "vanish"
    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config([_field(status), _field(reason, condition, order=1)]),
    )
    assert resp.status_code == 422, resp.text


async def test_an_empty_condition_is_accepted_and_inert(
    client, table_uuid, form_uuid, add_column
):
    """Empty means *always*, matching `apply_filters` on an empty filter group.

    A block left half-built in the editor has to be inert rather than hiding the field
    it was meant to reveal — which is what an empty condition read as "matches nothing"
    would do.
    """
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config(
            [_field(reason, {"when": {"conjunction": "and", "conditions": []}})]
        ),
    )
    assert resp.status_code == 200, resp.text
    stored = resp.json()["config"]["fields"][0]
    assert stored["condition"]["when"]["conditions"] == []


async def test_a_condition_naming_a_column_of_another_table_is_refused(
    client, workspace_uuid, table_uuid, form_uuid, add_column
):
    other = (
        await client.post(
            "/api/tables/",
            json={
                "name": "other",
                "display_name": "Other",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    stranger = await add_column(
        other, name="stranger", display_name="Stranger", type="text"
    )
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )

    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config([_field(reason, _when(stranger))]),
    )
    assert resp.status_code == 400, resp.text
    assert stranger in resp.json()["detail"]


async def test_a_condition_naming_a_deleted_column_is_refused(
    client, table_uuid, form_uuid, add_column
):
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )
    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{status}")
    ).status_code == 204

    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config([_field(reason, _when(status))]),
    )
    assert resp.status_code == 400, resp.text


async def test_a_field_cannot_condition_on_itself(
    client, table_uuid, form_uuid, add_column
):
    """It is not a cycle — conditions are one pass over the draft's raw values — but a
    field that hides on its own value keeps that value, so it never comes back."""
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config([_field(reason, _when(reason))]),
    )
    assert resp.status_code == 400, resp.text
    assert "own column" in resp.json()["detail"]


async def test_a_condition_may_name_a_column_the_form_excludes(
    client, table_uuid, form_uuid, add_column
):
    """Excluded from the layout is not excluded from the record.

    A field the form does not show still has a value — a default, or whatever an
    earlier edit left — so testing it is meaningful, and refusing to would rule out
    "ask for a reason only when the imported status says Rejected".
    """
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    reason = await add_column(
        table_uuid, name="reason", display_name="Reason", type="text"
    )
    resp = await client.patch(
        f"/api/tables/{table_uuid}/forms/{form_uuid}",
        json=_config(
            [
                _field(status, included=False),
                _field(reason, _when(status), order=1),
            ]
        ),
    )
    assert resp.status_code == 200, resp.text


async def test_a_condition_does_not_change_what_a_write_requires(
    client, table_uuid, form_uuid, add_column
):
    """The line this feature must not blur: conditions are presentational.

    A hidden required column is still required, because `_validate_data` never reads a
    form. That is what makes the editor's warning necessary rather than decorative, and
    it is why hiding a field can never be a way to bypass validation.
    """
    status = await add_column(
        table_uuid, name="status", display_name="Status", type="text"
    )
    reason = await add_column(
        table_uuid,
        name="reason",
        display_name="Reason",
        type="text",
        config={"required": True},
    )
    assert (
        await client.patch(
            f"/api/tables/{table_uuid}/forms/{form_uuid}",
            json=_config([_field(status), _field(reason, _when(status), order=1)]),
        )
    ).status_code == 200

    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {status: "Approved"}}
    )
    assert resp.status_code == 400, resp.text
    assert "required" in resp.json()["detail"]
