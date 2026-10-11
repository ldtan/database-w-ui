"""`allow_other` on a choice column, over the HTTP API.

With it on, the column's `options` are what it *offers* rather than what it
permits: a record may answer with something of its own, and that answer is stored
as the typed string itself — nothing on disk marks it as a write-in (D3 in the
data-model doc). That is the whole point, and it is what these tests pin: an
"other" answer has to be indistinguishable from an option to everything
downstream, so it filters, sorts and exports without a single case of its own.

What is *not* free-form is emptiness. A blank answer is an empty cell spelled
awkwardly, so it is refused rather than stored as "", which would make a required
column satisfiable with whitespace.
"""
import json

import pytest


async def _status_column(add_column, table_uuid: str, **config) -> str:
    return await add_column(
        table_uuid,
        name="status",
        display_name="Status",
        type="choice",
        config={"choices": "Todo;Doing;Done", **config},
    )


async def _write(client, table_uuid: str, col: str, value):
    return await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: value}}
    )


async def test_off_list_answer_is_accepted_and_stored_verbatim(
    client, table_uuid, add_column
):
    col = await _status_column(add_column, table_uuid, allow_other=True)
    resp = await _write(client, table_uuid, col, "Blocked on vendor")
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "Blocked on vendor"


async def test_off_list_answer_is_refused_without_the_flag(
    client, table_uuid, add_column
):
    col = await _status_column(add_column, table_uuid)
    resp = await _write(client, table_uuid, col, "Blocked on vendor")
    assert resp.status_code == 400
    assert "not a valid option" in resp.json()["detail"]


async def test_options_still_work_and_case_is_still_the_value(
    client, table_uuid, add_column
):
    """A near-miss on an option is not a correction — it is a different answer.

    Nothing folds case or trims towards an option, because the stored string *is*
    the value; "todo" beside "Todo" is two answers, exactly as it would be in a
    text column.
    """
    col = await _status_column(add_column, table_uuid, allow_other=True)
    assert (await _write(client, table_uuid, col, "Todo")).status_code == 201
    resp = await _write(client, table_uuid, col, "todo")
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "todo"


@pytest.mark.parametrize("value", [7, True, {"other": "x"}])
async def test_an_answer_still_has_to_be_words(client, table_uuid, add_column, value):
    """Anything but a string is a bad request, including one that cannot be hashed
    — a dict tested for membership of the option set raises rather than fails."""
    col = await _status_column(add_column, table_uuid, allow_other=True)
    resp = await _write(client, table_uuid, col, value)
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize("value", ["", "   "])
async def test_a_blank_answer_empties_the_cell(client, table_uuid, add_column, value):
    """Not a validation error: blank means empty for every column type, decided in
    one place on the way in, so `allow_other` does not turn whitespace into a value
    a required column would accept."""
    col = await _status_column(add_column, table_uuid, allow_other=True)
    resp = await _write(client, table_uuid, col, value)
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] is None


async def test_an_answer_is_stored_trimmed(client, table_uuid, add_column):
    """So padding cannot mint a value that looks identical to an option."""
    col = await _status_column(add_column, table_uuid, allow_other=True)
    resp = await _write(client, table_uuid, col, "  Todo  ")
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "Todo"


async def test_multiple_mixes_options_and_an_answer_of_its_own(
    client, table_uuid, add_column
):
    col = await _status_column(add_column, table_uuid, allow_other=True, multiple=True)
    resp = await _write(client, table_uuid, col, ["Todo", "Waiting on legal"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == ["Todo", "Waiting on legal"]
    # One bad entry fails the cell, as it would without the flag.
    assert (await _write(client, table_uuid, col, ["Todo", ""])).status_code == 400


async def test_default_may_be_off_list_only_with_the_flag(client, table_uuid):
    body = {
        "name": "status",
        "display_name": "Status",
        "type": "choice",
        "config": {"choices": "Todo;Done", "default": "Doing", "allow_other": True},
    }
    resp = await client.post(f"/api/tables/{table_uuid}/columns/", json=body)
    assert resp.status_code == 201, resp.text

    body["name"] = body["display_name"] = "status2"
    body["config"] = {"choices": "Todo;Done", "default": "Doing"}
    resp = await client.post(f"/api/tables/{table_uuid}/columns/", json=body)
    assert resp.status_code == 400
    assert "not one of" in resp.json()["detail"].lower()


async def test_a_blank_default_is_refused(client, table_uuid):
    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": "status",
            "display_name": "Status",
            "type": "choice",
            "config": {"choices": "Todo;Done", "default": "  ", "allow_other": True},
        },
    )
    assert resp.status_code == 400, resp.text


async def test_turning_it_off_keeps_the_answers_but_refuses_new_ones(
    client, table_uuid, add_column
):
    """The same deal a removed option strikes (known-gaps #4).

    Records outlive the setting that let them be written: the value stays and
    still displays, but the next write of that cell has to pick an option.
    """
    col = await _status_column(add_column, table_uuid, allow_other=True)
    created = await _write(client, table_uuid, col, "Blocked on vendor")
    record = created.json()["uuid"]

    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"allow_other": False}},
    )
    assert resp.status_code == 200, resp.text

    stored = await client.get(f"/api/tables/{table_uuid}/records/{record}")
    assert stored.json()["data"][col] == "Blocked on vendor"
    assert (await _write(client, table_uuid, col, "Blocked on vendor")).status_code == 400


async def test_turning_it_off_refuses_a_default_it_leaves_stranded(
    client, table_uuid, add_column
):
    """A merged config PATCH must not leave a default the column now rejects."""
    col = await _status_column(add_column, table_uuid, allow_other=True, default="Later")
    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"allow_other": False}},
    )
    assert resp.status_code == 400
    assert "not one of" in resp.json()["detail"].lower()


async def test_an_other_answer_filters_and_sorts_like_any_other_value(
    client, table_uuid, add_column
):
    """The payoff for storing the label itself: no filtering case of its own."""
    col = await _status_column(add_column, table_uuid, allow_other=True)
    for value in ("Todo", "Blocked on vendor", "Done"):
        assert (await _write(client, table_uuid, col, value)).status_code == 201

    filters = json.dumps(
        {
            "conjunction": "and",
            "conditions": [
                {"column_uuid": col, "operator": "is", "value": "Blocked on vendor"}
            ],
        }
    )
    resp = await client.get(
        f"/api/tables/{table_uuid}/records/", params={"filters": filters}
    )
    assert resp.status_code == 200, resp.text
    assert [r["data"][col] for r in resp.json()] == ["Blocked on vendor"]

    resp = await client.get(
        f"/api/tables/{table_uuid}/records/",
        params={"sort_by": col, "sort_dir": "asc"},
    )
    assert resp.status_code == 200, resp.text
    assert [r["data"][col] for r in resp.json()] == [
        "Blocked on vendor",
        "Done",
        "Todo",
    ]
