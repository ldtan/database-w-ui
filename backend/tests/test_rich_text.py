"""Rich text cells, over the HTTP API.

A `text` column in `rich` mode stores HTML that the UI renders as HTML, which
makes the stored value a security boundary in the same way a `link` column's
scheme is. The load-bearing claim of these tests is *where* that boundary sits:
sanitising happens on the write path in TableRecordService, so it holds for a
direct API call and a CSV import, not only for what the editor produces. Every
test here goes straight at the API — none of them go near the editor.
"""
import pytest


async def _add_rich_column(client, table_uuid: str, name="body", **config) -> str:
    resp = await client.post(
        f"/api/tables/{table_uuid}/columns/",
        json={
            "name": name,
            "display_name": name.title(),
            "type": "text",
            "config": {"text_mode": "rich", **config},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _write(client, table_uuid: str, col: str, value):
    resp = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {col: value}}
    )
    return resp


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<p>ok</p><script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<p onclick=\"alert(1)\">ok</p>",
        "<iframe src=\"https://evil.example\"></iframe>",
        "<svg><script>alert(1)</script></svg>",
        "<style>body{display:none}</style>",
        "<a href=\"javascript:alert(1)\">click</a>",
        "<a href=\"data:text/html,<script>alert(1)</script>\">click</a>",
    ],
)
async def test_dangerous_markup_never_reaches_storage(client, table_uuid, payload):
    col = await _add_rich_column(client, table_uuid)
    resp = await _write(client, table_uuid, col, payload)
    assert resp.status_code == 201, resp.text
    stored = resp.json()["data"][col] or ""
    for banned in ("<script", "<iframe", "<style", "onerror", "onclick", "javascript:"):
        assert banned not in stored.lower(), f"{banned!r} survived in {stored!r}"


async def test_formatting_survives(client, table_uuid):
    """Sanitising must not be so blunt that the feature stops working."""
    col = await _add_rich_column(client, table_uuid)
    value = (
        "<p><strong>bold</strong> <em>italic</em> <u>under</u></p>"
        "<ol><li data-table=\"bullet\">one</li><li data-table=\"ordered\">two</li></ol>"
        "<h2>heading</h2><blockquote>quoted</blockquote>"
        "<a href=\"https://example.com\">link</a>"
    )
    resp = await _write(client, table_uuid, col, value)
    assert resp.status_code == 201, resp.text
    stored = resp.json()["data"][col]
    for kept in (
        "<strong>", "<em>", "<u>", "<ol>", "<li", "<h2>", "<blockquote>",
        'href="https://example.com"',
        # Quill 2 marks bullets with data-table on an <ol>; dropping it would
        # silently renumber every bullet table. themes.css reads it back.
        'data-table="bullet"',
    ):
        assert kept in stored, f"{kept!r} was stripped from {stored!r}"


async def test_quills_own_list_furniture_is_dropped(client, table_uuid):
    """Quill puts `<span class="ql-ui">` in each table record to draw the marker.
    It means nothing outside the editor, and keeping the tag without its class
    would leave an empty element in every record, so `span` is not allowed at all.
    Unwrapping keeps the text, so nothing a person typed is lost."""
    col = await _add_rich_column(client, table_uuid)
    value = (
        '<ol><li data-table="bullet">'
        '<span class="ql-ui" contenteditable="false"></span>one</li></ol>'
    )
    resp = await _write(client, table_uuid, col, value)
    stored = resp.json()["data"][col]
    assert "<span" not in stored
    assert "ql-ui" not in stored
    assert 'data-table="bullet"' in stored
    assert "one" in stored


async def test_a_cell_that_is_only_markup_reads_as_empty(client, table_uuid):
    col = await _add_rich_column(client, table_uuid)
    resp = await _write(client, table_uuid, col, "<script>alert(1)</script>")
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] is None


async def test_an_update_is_sanitised_too(client, table_uuid):
    """Not just create: the same choke point covers PATCH."""
    col = await _add_rich_column(client, table_uuid)
    resp = await _write(client, table_uuid, col, "<p>fine</p>")
    record = resp.json()["uuid"]

    resp = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}",
        json={"data": {col: "<p>fine</p><script>alert(1)</script>"}},
    )
    assert resp.status_code == 200, resp.text
    assert "<script" not in resp.json()["data"][col]


async def test_a_csv_import_is_sanitised_too(client, table_uuid):
    """The other write path into a table, and the one an editor never touches."""
    col = await _add_rich_column(client, table_uuid, name="body")
    csv = 'body\n"<p>hi</p><script>alert(1)</script>"\n'
    resp = await client.post(
        f"/api/tables/{table_uuid}/import",
        files={"file": ("rows.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1, resp.text

    resp = await client.get(f"/api/tables/{table_uuid}/records/")
    rows = resp.json()
    assert rows, "import created nothing"
    stored = rows[0]["data"].get(col) or ""
    assert "<script" not in stored.lower()
    assert "hi" in stored


async def test_length_limits_count_visible_text_not_markup(client, table_uuid):
    """A limit is about how much someone may write, not how much they formatted."""
    col = await _add_rich_column(client, table_uuid, max_length=10)

    # 8 visible characters wrapped in far more than 10 characters of markup.
    resp = await _write(
        client, table_uuid, col, "<p><strong><em>12345678</em></strong></p>"
    )
    assert resp.status_code == 201, resp.text

    resp = await _write(client, table_uuid, col, "<p>123456789012</p>")
    assert resp.status_code == 400, resp.text
    assert "at most 10" in resp.text


async def test_blocks_are_not_run_together_when_measured(client, table_uuid):
    """"<p>ab</p><p>cd</p>" is four characters of text and one word boundary."""
    col = await _add_rich_column(client, table_uuid, max_length=4)
    # "ab cd" -> 5 with the boundary, so this is over the limit.
    resp = await _write(client, table_uuid, col, "<p>ab</p><p>cd</p>")
    assert resp.status_code == 400, resp.text


async def test_a_plain_text_column_is_left_alone(client, table_uuid, add_column):
    """Only `rich` is HTML. A single-line column stores exactly what it was sent,
    angle brackets and all — it is never rendered as markup.

    True only for as long as the column stays plain, which is what the next tests are
    about: `text_mode` is editable, and the moment it turns rich these same bytes
    become markup the grid renders."""
    col = await add_column(
        table_uuid, name="plain", display_name="Plain", type="text", config={}
    )
    resp = await _write(client, table_uuid, col, "<script>alert(1)</script>")
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"][col] == "<script>alert(1)</script>"


# -- turning a column rich after the fact ---------------------------------
#
# Sanitising happens on write, keyed on the column's mode *at that moment*. So the
# guarantee the renderer leans on ("anything under a rich column has been sanitised")
# is not established by the write path alone — a plain column collects raw markup
# perfectly legitimately, and then someone flips the switch. See columns/resanitize.py.


async def _turn_rich(client, table_uuid: str, col: str):
    return await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"text_mode": "rich"}},
    )


async def _read_cell(client, table_uuid: str, record: str, col: str):
    resp = await client.get(f"/api/tables/{table_uuid}/records/{record}")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"][col]


async def test_turning_a_column_rich_sanitises_what_it_already_holds(
    client, table_uuid, add_column
):
    """The bug this guards: markup banked while the column was plain, rendered as
    HTML the moment the column becomes rich."""
    col = await add_column(
        table_uuid, name="notes", display_name="Notes", type="text", config={}
    )
    created = await _write(client, table_uuid, col, "<img src=x onerror=alert(1)>ok")
    assert created.status_code == 201, created.text
    record = created.json()["uuid"]

    assert (await _turn_rich(client, table_uuid, col)).status_code == 200

    stored = await _read_cell(client, table_uuid, record, col)
    assert "onerror" not in stored.lower(), stored
    assert "<img" not in stored.lower(), stored
    # Unwrapped, not deleted: the words somebody typed survive the cleanup.
    assert "ok" in stored


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<iframe src=\"https://evil.example\"></iframe>",
        "<a href=\"javascript:alert(1)\">click</a>",
        "<p onclick=\"alert(1)\">hi</p>",
    ],
)
async def test_no_dangerous_markup_survives_the_switch(
    client, table_uuid, add_column, payload
):
    col = await add_column(
        table_uuid, name="body", display_name="Body", type="text", config={}
    )
    created = await _write(client, table_uuid, col, payload)
    assert created.status_code == 201, created.text
    record = created.json()["uuid"]

    assert (await _turn_rich(client, table_uuid, col)).status_code == 200

    stored = (await _read_cell(client, table_uuid, record, col)) or ""
    for banned in ("<script", "<iframe", "onclick", "javascript:"):
        assert banned not in stored.lower(), f"{banned!r} survived in {stored!r}"


async def test_the_sweep_reaches_every_record_not_just_the_first_page(
    client, table_uuid, add_column
):
    """A sweep that stopped at one chunk would leave most of a real table armed.

    Deliberately more rows than one read can hold, with the payload on the *last* of
    them, so a sweep that only did its first chunk fails here rather than passing by
    luck.
    """
    from src.tables.columns.resanitize import CHUNK

    col = await add_column(
        table_uuid, name="body", display_name="Body", type="text", config={}
    )
    for i in range(CHUNK + 5):
        resp = await _write(client, table_uuid, col, f"row {i}")
        assert resp.status_code == 201, resp.text
    last = await _write(client, table_uuid, col, "<script>alert(1)</script>")
    assert last.status_code == 201, last.text
    record = last.json()["uuid"]

    assert (await _turn_rich(client, table_uuid, col)).status_code == 200

    stored = (await _read_cell(client, table_uuid, record, col)) or ""
    assert "<script" not in stored.lower(), stored


async def test_a_binned_record_is_swept_too(client, table_uuid, add_column):
    """A record in the bin can be restored, so it must not come back holding markup
    the column now renders."""
    col = await add_column(
        table_uuid, name="body", display_name="Body", type="text", config={}
    )
    created = await _write(client, table_uuid, col, "<script>alert(1)</script>")
    record = created.json()["uuid"]
    assert (
        await client.delete(f"/api/tables/{table_uuid}/records/{record}")
    ).status_code == 204

    assert (await _turn_rich(client, table_uuid, col)).status_code == 200

    restored = await client.post(f"/api/tables/{table_uuid}/records/{record}/restore")
    assert restored.status_code == 200, restored.text
    assert "<script" not in (restored.json()["data"][col] or "").lower()


async def test_an_ordinary_edit_to_a_rich_column_leaves_values_alone(
    client, table_uuid
):
    """The sweep fires on the transition, not on every config PATCH. A column that was
    already rich holds values that were sanitised on their own writes, and re-cleaning
    them on an unrelated edit would be pure cost."""
    col = await _add_rich_column(client, table_uuid)
    created = await _write(client, table_uuid, col, "<p><strong>kept</strong></p>")
    record = created.json()["uuid"]

    resp = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"text_mode": "rich", "max_length": 500}},
    )
    assert resp.status_code == 200, resp.text

    assert "<strong>" in await _read_cell(client, table_uuid, record, col)


async def test_going_rich_then_plain_then_rich_sanitises_the_second_time(
    client, table_uuid, add_column
):
    """Every transition into rich sweeps, not just the first.

    Otherwise the switch becomes a one-shot guard: go plain, write markup the plain
    column accepts verbatim, come back to rich, and the values are armed again.
    """
    col = await add_column(
        table_uuid, name="body", display_name="Body", type="text", config={}
    )
    assert (await _turn_rich(client, table_uuid, col)).status_code == 200

    back_to_plain = await client.patch(
        f"/api/tables/{table_uuid}/columns/{col}",
        json={"config": {"text_mode": "single"}},
    )
    assert back_to_plain.status_code == 200, back_to_plain.text

    created = await _write(client, table_uuid, col, "<script>alert(1)</script>")
    assert created.status_code == 201, created.text
    record = created.json()["uuid"]

    assert (await _turn_rich(client, table_uuid, col)).status_code == 200

    stored = (await _read_cell(client, table_uuid, record, col)) or ""
    assert "<script" not in stored.lower(), stored
