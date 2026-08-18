"""Answer a metric in Postgres instead of in Python, where the formula allows it.

The general path (`MetricService.evaluate`) streams every visible record into
Python, shapes it, and folds it into an accumulator. That is what makes arbitrary
formulas possible, and it costs a per-record trip through the interpreter — on
this deployment the app's CPU is the scarce resource and the database's is not
(`docs/capacity-and-performance.md`), so a whole-list scan is spent on exactly the
wrong side.

Most real metrics do not need any of that generality. ``SUM(amount)``,
``COUNT()``, ``AVG(hours)``, ``SUM(qty * price)`` are all things Postgres already
knows how to do over a JSONB column, and doing them there means **no record ever
becomes a Python object**. This module compiles that subset; anything it cannot
compile comes back as ``None`` and the caller scans.

Three rules keep this from becoming a second, quietly disagreeing evaluator:

1. **Only the aggregate call compiles — never the arithmetic around it.** A
   compiled call yields one number, and those numbers go through
   `aggregate.fold` exactly as the scanned ones do. So ``ROUND(SUM(a) / COUNT(),
   2)``, ``IF``, ``IS_NULL`` and the blank-propagation rules keep their single
   implementation, and this file only ever has to be right about *what one
   aggregate is over what cells*.

2. **All or nothing per request** (`compile_all`). One uncompilable metric puts
   the whole strip back on the scan. The alternative — compiled cards answered
   over every record while scanned ones stop at `METRIC_ROW_LIMIT` — would put
   two numbers describing two different record sets side by side, with only one
   of them carrying the truncation note. Cards agreeing with each other is the
   property the single-pass design exists to provide; it is not worth trading for
   a partial speed-up.

3. **When in doubt, refuse.** Every case below where SQL semantics might differ
   from the Python ones by even an edge is left to Python. A metric that is
   merely slow is a known cost; a metric that is quietly wrong is the failure
   mode this whole feature is built to avoid.

**What compiles.** ``SUM``, ``AVG``, ``MIN``, ``MAX``, ``COUNT`` (with and
without an argument) and ``EMPTY``, over a bare column or over ``+``/``-``/``*``
arithmetic on columns and numeric literals.

**What does not, and why each one:**

- ``MEDIAN`` and ``UNIQUE``. Both answer through *equality or ordering between
  values*, and the cells round-trip through JSONB text on the way. ``1`` and
  ``1.0`` are one element of a Python ``set`` and two distinct strings in SQL.
  `percentile_cont` interpolates in floating point where `_Accumulator` averages
  the two middle values exactly. Neither difference is large, and both are the
  kind that shows up as a number nobody can explain. They are also the two
  rarest aggregates, so the trade is cheap.
- **Division**, inside an aggregate's argument. Python answers a row that divides
  by zero with a blank and carries on (`ARITHMETIC_ERRORS` in
  `columns.formula`); Postgres raises `division_by_zero` and takes the entire
  statement — and with it every card on the strip — down with it. Division
  *outside* the aggregate is unaffected: that is folded in Python.
- **Row functions** — ``ROUND``, ``IF``, ``CONCAT``, ``IS_NULL`` and the rest —
  inside an aggregate's argument. They are expressible in SQL, but each one is a
  fresh chance for a semantic difference, and their whole point is per-record
  logic that the scan already handles correctly.
- **`calculated` columns.** Their stored value is a query index that may lag a
  formula edit (`docs/known-gaps.md` §5), which is why every read recomputes it.
  Totalling the stored copy is exactly the silently-wrong number the scan exists
  to prevent, so aggregating a calculated column still means scanning.
- **`multiple` columns.** A multi-valued cell shapes to a Python ``list``, which
  the row grammar reads as "no value" — so the scan answers ``0``/blank for them
  today. Reproducing that in SQL would mean encoding an oddity rather than a
  rule; refusing leaves the existing behaviour untouched.

**Arithmetic differs in Postgres's favour.** `numeric` is exact decimal where
Python's floats are binary, so a compiled ``SUM(price * 1.1)`` can differ from a
scanned one in the last bits — the compiled answer being the more accurate of the
two. The gap is far below the 1e-6 the frontend already rounds to.
"""
import ast
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import Numeric, case, cast, func, literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..columns.enums import NUMERIC_TYPES, ColumnType
from ..columns.models import Column
from ..items.models import ListItem
from . import aggregate

# Aggregate kinds this module knows how to write in SQL. `MEDIAN` and `UNIQUE`
# are deliberately absent — see the module docstring.
PUSHABLE = frozenset({"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_ROWS", "EMPTY"})

# Column types whose cells this module will read. `calculated` is excluded even
# though a metric may reference one; the module docstring says why.
_PUSHABLE_TYPES = frozenset(NUMERIC_TYPES | {ColumnType.text})

# The JSONB types the row grammar reads as a value. `ast.Name` evaluation in
# `columns.formula` keeps `int`, `float` and `str` and answers `None` for
# everything else — booleans included — so these two are the whole set.
_VALUE_TYPES = ("number", "string")


def _parts(column: Column):
    """One cell's JSONB type and text, after the same unwrapping a reader does.

    `coerce_shape` takes the first element of an array held in a column that does
    not declare `multiple` — records outlive that setting, and a legacy array
    would otherwise read as its serialized text. `filtering._field` reproduces
    that rule in SQL for filters and this reproduces it for aggregates; the three
    have to agree or a total disagrees with the grid it sits above.

    Returned as a (type, text) pair rather than one expression because the callers
    below need to *test* the type and *use* the text, and `.astext` has to be
    taken on a real JSONB expression rather than on a `CASE` over two of them.
    """
    cell = ListItem.data[str(column.uuid)]
    element = cell[0]
    is_array = func.jsonb_typeof(cell) == "array"
    kind = case((is_array, func.jsonb_typeof(element)), else_=func.jsonb_typeof(cell))
    text = case((is_array, element.astext), else_=cell.astext)
    return kind, text


def _value(column: Column) -> ColumnElement:
    """The cell where it holds a value the row grammar would read, else NULL.

    This is what `COUNT` and `EMPTY` ask about, and it has to be NULL in exactly
    the cases the scan produces `None`: a missing key, a JSON ``null``, a boolean,
    an object — and an empty array, which unwraps to a missing element.
    """
    kind, text = _parts(column)
    return case((kind.in_(_VALUE_TYPES), text), else_=None)


def _number(column: Column) -> ColumnElement:
    """The cell as a number, or NULL where it does not hold one.

    The `jsonb_typeof` guard is not an optimisation, it is the only safe form:
    `CAST('ana' AS numeric)` does not blank a row, it fails the whole statement.
    It also happens to be exactly the scan's rule — `_Accumulator.add` drops any
    value `_is_num` rejects — so a text cell is skipped identically either way.

    Unlike `filtering._numeric` this guards a `number` column too. There, a
    declared number column can be cast outright because writes enforce it; here
    the cost of being wrong is a 500 on the whole metric strip rather than one
    misplaced row, and the guard is free.
    """
    kind, text = _parts(column)
    return case((kind == "number", text), else_=None).cast(Numeric)


def _pushable(column: Optional[Column]) -> bool:
    return (
        column is not None
        and column.type in _PUSHABLE_TYPES
        and not (column.config or {}).get("multiple")
    )


def _arithmetic(node: ast.AST, by_name: dict[str, Column]) -> Optional[ColumnElement]:
    """A row expression as a numeric SQL expression, or ``None`` if it will not go.

    NULL propagates through SQL arithmetic exactly as ``None`` propagates through
    `columns.formula._eval` — one blank operand blanks the result, and a blank is
    then skipped by the aggregate rather than counted as zero. That correspondence
    is what makes this safe to compile term by term.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return None  # a text literal in arithmetic blanks the row in Python too
        return cast(literal(node.value), Numeric)

    if isinstance(node, ast.Name):
        column = by_name.get(node.id)
        return _number(column) if _pushable(column) else None

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _arithmetic(node.operand, by_name)
        if operand is None:
            return None
        return operand if isinstance(node.op, ast.UAdd) else -operand

    # Add/Sub/Mult only. Div is absent on purpose — see the module docstring.
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
        left = _arithmetic(node.left, by_name)
        right = _arithmetic(node.right, by_name)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        return left * right

    return None


def _compile_call(
    call: ast.Call, by_name: dict[str, Column]
) -> Optional[ColumnElement]:
    """One aggregate call as a SQL aggregate expression, or ``None``."""
    kind = aggregate.call_kind(call)
    if kind not in PUSHABLE:
        return None

    if kind == "COUNT_ROWS":
        return func.count()

    argument = call.args[0]
    if kind in ("COUNT", "EMPTY"):
        # These ask whether a cell has a value at all, so a bare column reads as
        # its value (text included) rather than being forced through a numeric
        # guard that would drop every string. Anything else is arithmetic, whose
        # NULL-ness already answers the same question.
        if isinstance(argument, ast.Name):
            column = by_name.get(argument.id)
            if not _pushable(column):
                return None
            expression = _value(column)
        else:
            expression = _arithmetic(argument, by_name)
            if expression is None:
                return None
        if kind == "COUNT":
            return func.count(expression)
        # EMPTY counts the rows COUNT skips, which is every row minus those.
        return func.count() - func.count(expression)

    expression = _arithmetic(argument, by_name)
    if expression is None:
        return None
    if kind == "SUM":
        # `SUM` over no rows is 0, not blank — a total of nothing is nothing —
        # and SQL's answer there is NULL, which would read as an empty card.
        return func.coalesce(func.sum(expression), 0)
    if kind == "AVG":
        return func.avg(expression)
    return func.min(expression) if kind == "MIN" else func.max(expression)


def compile_all(
    formulas: Sequence[aggregate.MetricFormula], columns: dict[str, Column]
) -> Optional[dict[int, ColumnElement]]:
    """Every aggregate in these formulas as SQL, or ``None`` if any one will not go.

    ``columns`` is keyed by column *name*, which is how a formula spells one.
    All-or-nothing by design — see rule 2 in the module docstring.
    """
    compiled: dict[int, ColumnElement] = {}
    for formula in formulas:
        for call in formula.calls:
            expression = _compile_call(call, columns)
            if expression is None:
                return None
            compiled[id(call)] = expression
    return compiled


def _python(value: object) -> object:
    """A driver value as the scan would have produced it.

    Postgres answers `numeric` aggregates with `Decimal`, which `normalize_result`
    does not recognise and would turn into ``None``. An integral one becomes an
    `int` so a whole-number total keeps reading as a whole number, matching both
    the scan and the `int`-first union in `MetricValue.value`.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


async def run(
    db: AsyncSession, stmt, compiled: dict[int, ColumnElement]
) -> tuple[dict[int, object], int]:
    """Answer every compiled aggregate, and count the records, in one query.

    ``stmt`` is the statement `ListItemService.visible_stmt` built, so the
    permission filter, the view, the search and the filters all still decide which
    records are in scope — the compiled path changes *how* the answer is computed,
    never *what it is over*.

    The count rides along in the same select rather than costing a second query,
    and it is the true total: nothing is capped here, because nothing accumulates
    in Python to cap.
    """
    keys = list(compiled)
    row = (
        await db.execute(
            stmt.with_only_columns(func.count(), *(compiled[key] for key in keys))
            # `with_only_columns` recomputes the FROM list from what is left, so
            # name the table rather than leaning on inference: `count(*)` carries
            # no table of its own, and a metric strip of nothing but `COUNT()`
            # would otherwise have no FROM at all.
            .select_from(ListItem)
        )
    ).one()
    return {key: _python(value) for key, value in zip(keys, row[1:])}, int(row[0])
