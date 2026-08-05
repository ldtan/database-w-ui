"""Metrics answered by Postgres instead of by a Python scan.

`src.lists.metrics.pushdown` compiles the common aggregate shapes into SQL so a
whole-list total costs no per-record Python. That is only worth having if the two
paths are indistinguishable, so the load-bearing test here is the equivalence
one: every formula is asked twice — once compiled, once with the compiler
disabled — and the answers must match exactly. A performance change that alters a
number is not a performance change.

The rest pin the boundary itself: what compiles, what deliberately does not, and
the two visible consequences of compiling (a true `row_count`, and no cap).
"""
import pytest
import pytest_asyncio

from src.lists.metrics import pushdown, services

from test_metrics import add_metric, value_of  # noqa: F401  (shared helpers)


@pytest.fixture
def no_pushdown(monkeypatch):
    """Force the Python scan, whatever the formula would otherwise allow."""
    monkeypatch.setattr(pushdown, "compile_all", lambda *args, **kwargs: None)


@pytest_asyncio.fixture
async def ledger(client, list_uuid, add_column):
    """Numbers, text, a calculated column, and rows that exercise the edges.

    One record leaves `qty` empty and one leaves `note` empty, because "a blank is
    skipped, never counted as zero" is the rule most likely to drift between an
    accumulator and a SQL aggregate.
    """
    qty = await add_column(list_uuid, name="qty", display_name="Qty", type="number")
    price = await add_column(list_uuid, name="price", display_name="Price", type="number")
    note = await add_column(list_uuid, name="note", display_name="Note", type="text")
    line = await add_column(
        list_uuid,
        name="line",
        display_name="Line",
        type="calculated",
        config={"formula": "qty * price"},
    )
    for values in (
        {qty: 2, price: 10, note: "first"},
        {qty: 5, price: 3, note: "second"},
        {qty: 1, price: 10, note: None},
        {qty: None, price: 7, note: "fourth"},
    ):
        resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": values})
        assert resp.status_code == 201, resp.text
    return {"list": list_uuid, "qty": qty, "price": price, "note": note, "line": line}


async def values(client, list_uuid):
    resp = await client.get(f"/api/lists/{list_uuid}/metrics/values")
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------
# The two paths agree
# --------------------------------------------------------------------------

# Formula, and the answer both paths owe. Chosen to cover every compiled
# aggregate, blanks, text where a number is wanted, and outer arithmetic sitting
# above a compiled call.
EQUIVALENT = [
    ("SUM(qty)", 8),
    ("SUM(qty * price)", 45),
    # The blank `qty` blanks its whole row rather than reading as 0, so the row
    # priced 7 contributes nothing: 8 + (-2) + 9.
    ("SUM(price - qty)", 15),
    ("AVG(qty)", pytest.approx(8 / 3)),
    ("MIN(qty)", 1),
    ("MAX(price)", 10),
    ("COUNT()", 4),
    ("COUNT(qty)", 3),
    ("COUNT(note)", 3),
    ("EMPTY(qty)", 1),
    ("EMPTY(note)", 1),
    # A text column where a number is wanted: every cell is skipped, so the total
    # of nothing is 0 and the average of nothing is blank.
    ("SUM(note)", 0),
    ("AVG(note)", None),
    ("MIN(note)", None),
    # Outer arithmetic and row functions above the aggregates, folded in Python on
    # both paths — the compiled numbers have to arrive in the same shapes.
    ("SUM(qty) / COUNT()", 2),
    ("ROUND(AVG(qty), 1)", 2.7),
    ("IF(IS_NULL(AVG(note)), 0, AVG(note))", 0),
    ("SUM(qty) * 2 - 1", 15),
    # A literal inside the aggregate, and a unary minus.
    ("SUM(qty * 1.5)", 12),
    ("SUM(-qty)", -8),
]


@pytest.mark.parametrize("formula,expected", EQUIVALENT)
async def test_compiled_answer_matches_the_scan(client, ledger, formula, expected):
    """The whole bargain: same question, same answer, whichever path runs it."""
    lst = ledger["list"]
    assert (await add_metric(client, lst, "m", formula)).status_code == 201

    compiled = await values(client, lst)
    assert value_of(compiled, "m") == expected

    # Now the same request with the compiler disabled. Done through the module
    # attribute the service reads, so this exercises the real fallback branch.
    original = pushdown.compile_all
    pushdown.compile_all = lambda *args, **kwargs: None
    try:
        scanned = await values(client, lst)
    finally:
        pushdown.compile_all = original
    assert value_of(scanned, "m") == expected
    assert value_of(scanned, "m") == value_of(compiled, "m")


async def test_the_formulas_above_really_do_compile(ledger):
    """Guards the test above from passing vacuously.

    If `compile_all` quietly stopped compiling anything, every case in
    EQUIVALENT would still pass — both runs would be the scan. This asserts the
    first half was in fact the compiled path.
    """
    from src.lists.metrics import aggregate

    columns = {
        name: _FakeColumn(name)
        for name in ("qty", "price", "note")
    }
    for formula, _ in EQUIVALENT:
        parsed = aggregate.parse(formula)
        assert pushdown.compile_all([parsed], columns) is not None, formula


class _FakeColumn:
    """The three attributes `pushdown` reads off a column."""

    def __init__(self, name, type_=None, config=None):
        from src.lists.columns.enums import ColumnType

        self.name = name
        self.uuid = "00000000-0000-0000-0000-00000000000f"
        self.type = type_ or (
            ColumnType.text if name == "note" else ColumnType.number
        )
        self.config = config or {}


# --------------------------------------------------------------------------
# The boundary: what does not compile, and why it is right that it does not
# --------------------------------------------------------------------------


def _compiles(formula: str, columns=None) -> bool:
    from src.lists.metrics import aggregate

    columns = columns or {n: _FakeColumn(n) for n in ("qty", "price", "note")}
    return pushdown.compile_all([aggregate.parse(formula)], columns) is not None


@pytest.mark.parametrize(
    "formula",
    [
        # Equality/ordering semantics that do not survive the round trip through
        # JSONB text — left to Python on purpose.
        "MEDIAN(qty)",
        "UNIQUE(note)",
        # Division inside an aggregate: Postgres would fail the whole statement on
        # a zero divisor where the scan blanks that one row and carries on.
        "SUM(qty / price)",
        # Row functions inside an aggregate: expressible, but each is a fresh
        # chance to disagree, and the scan already handles them.
        "SUM(ROUND(qty, 1))",
        "SUM(IF(qty > 2, qty, 0))",
        "COUNT(CONCAT(note, note))",
        "SUM(qty > 2)",
    ],
)
def test_these_stay_on_the_scan(formula):
    assert not _compiles(formula)


def test_a_calculated_column_is_never_compiled():
    """Its stored value is an index that may lag a formula edit, so a total has to
    recompute it per record rather than sum where it sits."""
    from src.lists.columns.enums import ColumnType

    columns = {
        "qty": _FakeColumn("qty"),
        "line": _FakeColumn("line", ColumnType.calculated, {"formula": "qty * 2"}),
    }
    assert _compiles("SUM(qty)", columns)
    assert not _compiles("SUM(line)", columns)
    assert not _compiles("COUNT(line)", columns)


def test_a_multiple_column_is_never_compiled():
    columns = {"qty": _FakeColumn("qty", None, {"multiple": True})}
    assert not _compiles("SUM(qty)", columns)


def test_one_uncompilable_metric_puts_the_whole_strip_on_the_scan():
    """All-or-nothing. Two cards answered over two different record sets — one
    capped, one not — is worse than both being slow."""
    from src.lists.metrics import aggregate

    columns = {n: _FakeColumn(n) for n in ("qty", "note")}
    formulas = [aggregate.parse("SUM(qty)"), aggregate.parse("MEDIAN(qty)")]
    assert pushdown.compile_all(formulas, columns) is None


async def test_a_calculated_metric_still_answers_via_the_scan(client, ledger):
    """The fallback is a fallback, not a refusal: the number still arrives."""
    lst = ledger["list"]
    assert (await add_metric(client, lst, "total_line", "SUM(line)")).status_code == 201
    assert value_of(await values(client, lst), "total_line") == 45


# --------------------------------------------------------------------------
# What compiling changes that a reader can see
# --------------------------------------------------------------------------


async def test_row_count_is_the_true_count_and_nothing_is_truncated(client, ledger):
    lst = ledger["list"]
    assert (await add_metric(client, lst, "m", "SUM(qty)")).status_code == 201
    payload = await values(client, lst)
    assert payload["row_count"] == 4
    assert payload["truncated"] is False


async def test_the_compiled_path_ignores_the_scan_row_limit(client, ledger, monkeypatch):
    """A cap on Python accumulation has nothing to bound when nothing accumulates.

    With the limit at 1, the scan would answer over a single record and say it was
    truncated; the compiled path answers over all four and does not.
    """
    monkeypatch.setattr(services, "METRIC_ROW_LIMIT", 1)
    lst = ledger["list"]
    assert (await add_metric(client, lst, "m", "SUM(qty)")).status_code == 201

    payload = await values(client, lst)
    assert value_of(payload, "m") == 8
    assert payload["row_count"] == 4
    assert payload["truncated"] is False


async def test_the_scan_still_honours_the_row_limit(client, ledger, monkeypatch, no_pushdown):
    monkeypatch.setattr(services, "METRIC_ROW_LIMIT", 1)
    lst = ledger["list"]
    assert (await add_metric(client, lst, "m", "SUM(qty)")).status_code == 201

    payload = await values(client, lst)
    assert payload["row_count"] == 1
    assert payload["truncated"] is True


# --------------------------------------------------------------------------
# Scope: compiling must not change *which* records a metric is over
# --------------------------------------------------------------------------


async def test_a_filter_narrows_the_compiled_answer(client, ledger):
    """The compiled query is built on `visible_stmt`, so the filters still apply."""
    lst, price = ledger["list"], ledger["price"]
    assert (await add_metric(client, lst, "m", "SUM(qty)")).status_code == 201

    filters = (
        '{"match":"all","conditions":[{"column_uuid":"%s","operator":"=","value":10}]}'
        % price
    )
    resp = await client.get(f"/api/lists/{lst}/metrics/values", params={"filters": filters})
    assert resp.status_code == 200, resp.text
    assert value_of(resp.json(), "m") == 3  # the two rows priced 10: qty 2 and 1
    assert resp.json()["row_count"] == 2


async def test_a_search_narrows_the_compiled_answer(client, ledger):
    lst = ledger["list"]
    assert (await add_metric(client, lst, "m", "COUNT()")).status_code == 201

    resp = await client.get(f"/api/lists/{lst}/metrics/values", params={"q": "second"})
    assert resp.status_code == 200, resp.text
    assert value_of(resp.json(), "m") == 1


async def test_an_empty_list_totals_to_zero_not_blank(client, list_uuid, add_column):
    """`SUM` of no records is 0 on both paths — SQL's own answer there is NULL,
    which would render as an empty card."""
    await add_column(list_uuid, name="qty", display_name="Qty", type="number")
    assert (await add_metric(client, list_uuid, "total", "SUM(qty)")).status_code == 201
    assert (await add_metric(client, list_uuid, "mean", "AVG(qty)")).status_code == 201

    payload = await values(client, list_uuid)
    assert payload["row_count"] == 0
    assert value_of(payload, "total") == 0
    assert value_of(payload, "mean") is None  # an average of nothing is not zero
