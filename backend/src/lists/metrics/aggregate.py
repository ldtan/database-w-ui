"""Aggregate formulas for list metrics — a second grammar over `columns.formula`.

A `calculated` column asks a question about **one record** (``qty * price``). A
metric asks one about **all of them** (``SUM(qty * price)``), so its language is
the row language with one level added on top:

- **Outer level** — arithmetic over *aggregate results*: ``SUM(revenue) - SUM(cost)``,
  ``ROUND(SUM(hours) / COUNT(), 2)``.
- **Inner level** — an aggregate's argument is an ordinary row expression, checked
  and evaluated by `columns.formula` itself. So ``SUM(qty * price)`` works for
  free, and every safety property of that module (no ``eval``, no builtins, no
  attributes, bounded length/depth/text) holds here unchanged.

A **bare column name at the outer level is refused**: ``hours`` is one value per
record, and a metric is one value in total, so the only sensible reading is an
aggregate the author forgot to write. Refusing it is what makes the two levels
unambiguous rather than merely conventional.

``MIN`` and ``MAX`` appear in both tables, and arity tells them apart: one
argument is the aggregate ("the smallest ``hours`` of any record"), several is
the row function applied here ("the smaller of these two totals"). Nothing is
lost to the overlap, because a one-argument row ``MIN`` is just its argument.

Blank data behaves as it does everywhere else in this app: a record whose
expression has no value is **skipped**, never counted as zero — ``AVG(cost)``
over three rows, one of them empty, is the average of two. Over *no* values,
``SUM`` and the counts answer ``0`` (a total of nothing is nothing) while
``AVG``/``MIN``/``MAX``/``MEDIAN`` answer blank, because there is no such number
to invent.

``IS_NULL`` and ``DEFAULT`` are therefore useful at both levels, and mean the same
thing at each. Inside an aggregate they are about one record's cell:
``SUM(IS_NULL(owner))`` is the count ``EMPTY(owner)`` gives, and ``AVG(DEFAULT(qty,
0))`` fills each gap *before* the average is taken, so the blanks drag it down
instead of being skipped — a different number from ``AVG(qty)``, and sometimes the
wanted one. Outside, they are about the aggregate's own result: ``DEFAULT(AVG(cost),
0)`` puts a number on a card that would otherwise be blank because there was nothing
to average.
"""
import ast
from typing import Optional, Sequence, Union

from src.core.exceptions import ValidationError

from ..columns.formula import (
    ARITHMETIC_ERRORS,
    MAX_DEPTH,
    MAX_SOURCE,
    ROW_FUNCTIONS,
    TOO_DEEP,
    check_row_expression,
    eval_row_expression,
    normalize_result,
)

# Aggregate name -> (min_args, max_args). Every one of these takes a single row
# expression, except COUNT, which counts records when given nothing to look at.
AGGREGATES: dict[str, tuple[int, int]] = {
    "SUM": (1, 1),
    "AVG": (1, 1),
    "COUNT": (0, 1),
    "EMPTY": (1, 1),
    "UNIQUE": (1, 1),
    "MIN": (1, 1),
    "MAX": (1, 1),
    "MEDIAN": (1, 1),
}

# Aggregates that answer with a number of records rather than a statistic over
# values, and so are meaningful for every column type rather than numbers only.
_COUNTING = frozenset({"COUNT", "COUNT_ROWS", "EMPTY", "UNIQUE"})

_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_UNARYOPS = (ast.UAdd, ast.USub, ast.Not)
_CMPOPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


class MetricFormula:
    """A parsed, validated metric formula.

    ``calls`` holds this formula's aggregate call nodes, and evaluation keys its
    accumulators by ``id()`` of those nodes. The tree and the nodes are reachable
    from this object and from nowhere else, so those ids stay valid — and stay
    unique — for exactly as long as the formula does.
    """

    __slots__ = ("source", "tree", "refs", "calls")

    def __init__(
        self,
        source: str,
        tree: ast.Expression,
        refs: frozenset[str],
        calls: list[ast.Call],
    ):
        self.source = source
        self.tree = tree
        self.refs = refs
        self.calls = calls


def parse(source: object) -> MetricFormula:
    """Parse and fully validate a metric formula, or raise ``ValidationError``.

    Returns the parsed form and the column names it reads, so the caller can
    check that each one still exists on the list before scanning anything.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValidationError("A metric requires a formula")
    if len(source) > MAX_SOURCE:
        raise ValidationError(f"A formula may be at most {MAX_SOURCE} characters")

    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValidationError(f"Formula is not a valid expression: {exc.msg}") from exc
    except RecursionError as exc:
        raise ValidationError(TOO_DEEP) from exc

    refs: set[str] = set()
    calls: list[ast.Call] = []
    _check(tree.body, refs, calls, 0)
    if not calls:
        allowed = ", ".join(sorted(AGGREGATES))
        raise ValidationError(
            f"A metric formula must use at least one aggregate ({allowed}); "
            "without one it never reads a record"
        )
    return MetricFormula(source, tree, frozenset(refs), calls)


def _check(node: ast.AST, refs: set[str], calls: list[ast.Call], depth: int) -> None:
    """Validate one node at the *aggregate* level; collect refs and aggregate calls.

    ``depth`` is threaded down into `check_row_expression` as well, so a formula
    is bounded once across both grammars rather than getting a fresh allowance
    each time it changes level.
    """
    if depth > MAX_DEPTH:
        raise ValidationError(TOO_DEEP)
    depth += 1

    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BINOPS):
            raise ValidationError("Only + - * / are allowed between values")
        _check(node.left, refs, calls, depth)
        _check(node.right, refs, calls, depth)

    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UNARYOPS):
            raise ValidationError("Unsupported unary operator in formula")
        _check(node.operand, refs, calls, depth)

    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            _check(value, refs, calls, depth)

    elif isinstance(node, ast.Compare):
        if not all(isinstance(op, _CMPOPS) for op in node.ops):
            raise ValidationError("Unsupported comparison in formula")
        _check(node.left, refs, calls, depth)
        for comparator in node.comparators:
            _check(comparator, refs, calls, depth)

    elif isinstance(node, ast.Call):
        if node.keywords or not isinstance(node.func, ast.Name):
            raise ValidationError("Formula functions take positional arguments only")
        name = node.func.id
        count = len(node.args)
        spec = AGGREGATES.get(name)
        # MIN/MAX sit in both tables; one argument means the aggregate. See the
        # module docstring for why the overlap costs nothing.
        if spec is not None and (name not in ROW_FUNCTIONS or count == 1):
            low, high = spec
            if count < low or count > high:
                want = f"{low}" if low == high else f"{low}-{high}"
                raise ValidationError(
                    f"Aggregate '{name}' takes {want} argument(s), got {count}"
                )
            # Everything below an aggregate is an ordinary row expression, so the
            # row grammar validates it — this is the only place the two meet.
            for arg in node.args:
                check_row_expression(arg, refs, depth)
            calls.append(node)

        elif name in ROW_FUNCTIONS:
            low, high = ROW_FUNCTIONS[name]
            if count < low or (high is not None and count > high):
                want = (
                    f"{low}" if low == high
                    else f"{low}+" if high is None
                    else f"{low}-{high}"
                )
                raise ValidationError(
                    f"Function '{name}' takes {want} argument(s), got {count}"
                )
            for arg in node.args:
                _check(arg, refs, calls, depth)

        else:
            allowed = ", ".join(sorted(set(AGGREGATES) | set(ROW_FUNCTIONS)))
            raise ValidationError(
                f"Unknown function '{name}' in formula; allowed: {allowed}"
            )

    elif isinstance(node, ast.Name):
        raise ValidationError(
            f"Wrap '{node.id}' in an aggregate, for example SUM({node.id}) — a "
            "metric is one value over every record, and a bare column name is "
            "one value per record"
        )

    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float, str)):
            raise ValidationError(
                "Only numbers and quoted text are allowed as literals in a formula"
            )

    else:
        raise ValidationError(
            "That expression is not allowed in a formula "
            "(no attributes, indexing, or names other than columns)"
        )


def column_refs(formula: MetricFormula) -> frozenset[str]:
    """The column names this formula reads.

    Every name lives inside an aggregate — `_check` refuses one anywhere else —
    so these are collected by the row grammar's own walk and carry its guarantee:
    value positions only, never a function name.
    """
    return formula.refs


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def call_kind(call: ast.Call) -> str:
    """The accumulator a call needs. ``COUNT()`` counts records rather than
    values, which is a different accumulator from ``COUNT(col)``, not a special
    case inside one.

    Public because `pushdown` decides what it can compile per *kind* and must
    split ``COUNT`` the same way this does; two spellings of that rule would
    eventually disagree about which one `COUNT()` is.
    """
    name = call.func.id
    return "COUNT_ROWS" if name == "COUNT" and not call.args else name


class _Accumulator:
    """One aggregate's running total over the records seen so far.

    Everything but MEDIAN and UNIQUE accumulates in constant space; those two
    have to remember values, which is one of the reasons the scan that feeds this
    is bounded (see `METRIC_ROW_LIMIT` in the service).
    """

    __slots__ = ("kind", "total", "count", "smallest", "largest", "values", "seen")

    def __init__(self, kind: str):
        self.kind = kind
        self.total: Union[int, float] = 0
        self.count = 0
        self.smallest = None
        self.largest = None
        self.values: Optional[list] = [] if kind == "MEDIAN" else None
        self.seen: Optional[set] = set() if kind == "UNIQUE" else None

    def add(self, value) -> None:
        if self.kind == "COUNT_ROWS":
            self.count += 1
            return
        if value is None:
            if self.kind == "EMPTY":
                self.count += 1
            return
        if isinstance(value, bool):
            # `SUM(qty > 5)` — "how many records pass" — is a spreadsheet idiom
            # worth keeping, and 1/0 is what a bare comparison already reads as.
            value = int(value)

        if self.kind == "EMPTY":
            return
        if self.kind == "COUNT":
            self.count += 1
            return
        if self.kind == "UNIQUE":
            self.seen.add(value)
            return
        if not _is_num(value):
            # Text is not part of a total or an average, the same way it blanks
            # the row functions that expect a number rather than being compared.
            return

        self.count += 1
        self.total += value
        if self.smallest is None or value < self.smallest:
            self.smallest = value
        if self.largest is None or value > self.largest:
            self.largest = value
        if self.values is not None:
            self.values.append(value)

    def result(self):
        if self.kind in _COUNTING:
            return len(self.seen) if self.kind == "UNIQUE" else self.count
        if self.kind == "SUM":
            return self.total  # 0 over no records: a total of nothing is nothing
        if self.count == 0:
            # An average, extreme or middle of no values is not 0, it is nothing.
            return None
        if self.kind == "AVG":
            return self.total / self.count
        if self.kind == "MIN":
            return self.smallest
        if self.kind == "MAX":
            return self.largest
        ordered = sorted(self.values)  # MEDIAN
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2


class MetricEvaluator:
    """Accumulates one pass over a list's records for several metrics at once.

    Built from the parsed formulas, fed every record in scope, then asked for
    each formula's value. Doing all of them together is the point: a list is
    scanned once however many cards it carries, and every card therefore reports
    on exactly the same set of records.
    """

    def __init__(self, formulas: Sequence[MetricFormula]):
        self._accumulators: dict[int, _Accumulator] = {}
        # (argument node or None, accumulator) — the per-row work, flattened once
        # so a record costs a loop over aggregates rather than a tree walk each.
        self._work: list[tuple[Optional[ast.AST], _Accumulator]] = []
        names: set[str] = set()
        for formula in formulas:
            names |= formula.refs
            for call in formula.calls:
                accumulator = _Accumulator(call_kind(call))
                self._accumulators[id(call)] = accumulator
                self._work.append(
                    (call.args[0] if call.args else None, accumulator)
                )
        self.column_names = frozenset(names)

    def add_row(self, values: dict[str, object]) -> None:
        """Fold one record in. ``values`` maps column *name* to that record's cell."""
        for argument, accumulator in self._work:
            if argument is None:
                accumulator.add(None)  # COUNT(): the record itself is the value
                continue
            try:
                accumulator.add(eval_row_expression(argument, values))
            except ARITHMETIC_ERRORS:
                # One unanswerable record must not fail the whole metric, the
                # same bargain `evaluate` makes for a cell: it reads as blank,
                # and a blank is skipped rather than counted as zero.
                accumulator.add(None)

    def results(self) -> dict[int, object]:
        """Each aggregate call's value, keyed by ``id()`` of its node.

        The seam `fold` consumes. Scanning records in Python is one way to fill
        this in and `pushdown` is the other, which is what lets a metric answered
        by Postgres and a metric answered by a scan share every line of the outer
        arithmetic below rather than growing a second copy of `IF`, `ROUND` and
        the blank-propagation rules.
        """
        return {key: acc.result() for key, acc in self._accumulators.items()}

    def result(self, formula: MetricFormula) -> Union[float, str, None]:
        """This formula's value over everything added so far."""
        return fold(formula, self.results())


def fold(formula: MetricFormula, results: dict[int, object]) -> Union[float, str, None]:
    """One formula's value, given every aggregate call's result.

    ``results`` is keyed by ``id()`` of the call nodes in `MetricFormula.calls` —
    see that class for why those ids are stable and unique for the formula's
    lifetime.
    """
    try:
        return normalize_result(_fold(formula.tree.body, results))
    except ARITHMETIC_ERRORS:
        return None


def _fold(node: ast.AST, results: dict[int, object]):
    """Reduce an outer node to a value.

    An aggregate call becomes its computed result; every other node is handed
    back to the *row* evaluator with its children already reduced to constants.
    So the arithmetic, the comparisons and the function semantics — including the
    text bounds that make them safe — stay defined in exactly one place, and this
    module only decides where the numbers come from.
    """
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Call):
        # Membership, not a truthy lookup: an aggregate legitimately results in
        # ``None`` (``AVG`` of nothing), and `.get` would send that back round as
        # a row function to be re-evaluated.
        if id(node) in results:
            return results[id(node)]
        # An argument that cannot be computed folds to "no value" rather than
        # being raised past this call. Every function blanks on a blank argument,
        # so for all but one this is the answer it would have reached anyway —
        # and `IS_NULL(SUM(a) / COUNT(b))` asks about exactly this condition, so
        # it has to be handed the blank rather than the exception.
        folded = []
        for arg in node.args:
            try:
                folded.append(ast.Constant(_fold(arg, results)))
            except ARITHMETIC_ERRORS:
                folded.append(ast.Constant(None))
        return eval_row_expression(
            ast.Call(func=node.func, args=folded, keywords=[]), {}
        )

    if isinstance(node, ast.UnaryOp):
        return eval_row_expression(
            ast.UnaryOp(op=node.op, operand=ast.Constant(_fold(node.operand, results))),
            {},
        )

    if isinstance(node, ast.BinOp):
        return eval_row_expression(
            ast.BinOp(
                left=ast.Constant(_fold(node.left, results)),
                op=node.op,
                right=ast.Constant(_fold(node.right, results)),
            ),
            {},
        )

    if isinstance(node, ast.BoolOp):
        return eval_row_expression(
            ast.BoolOp(
                op=node.op,
                values=[ast.Constant(_fold(v, results)) for v in node.values],
            ),
            {},
        )

    if isinstance(node, ast.Compare):
        return eval_row_expression(
            ast.Compare(
                left=ast.Constant(_fold(node.left, results)),
                ops=node.ops,
                comparators=[
                    ast.Constant(_fold(c, results)) for c in node.comparators
                ],
            ),
            {},
        )

    # Unreachable: parse() rejects every other node type up front.
    raise ValidationError("Unsupported expression in metric formula")
