"""A tiny, safe expression language for `calculated` columns.

A calculated column has no stored value. It carries a ``formula`` in its config
that references other columns of the same list **by name** (their slug, which is
already a valid Python identifier), and the value is computed at read time.

Safety is the whole point, so this does NOT use ``eval``. It parses the formula
to a Python AST once, refuses every node type it does not explicitly allow, and
then walks that tree itself. The only names it will resolve are the column values
handed to it; there is no path to a builtin, an attribute, or a subscript. What a
formula may *spend* is bounded too — its length, how deeply it nests, and how much
text it may produce — because this runs once per record on every read, so an
unbounded one is a way to spend the whole server. A malicious or mistaken formula
can at worst return a wrong number or nothing.

Supported: the four arithmetic operators and parentheses, unary minus, numeric
**and** quoted-string literals, comparisons (for conditions), ``and``/``or``/``not``,
and the functions ``MIN``, ``MAX``, ``ROUND``, ``ABS``, ``INT``, ``IF(cond, then,
else)``, ``IS_NULL(x)``, the string functions ``CONCAT``, ``UPPER``, ``LOWER``,
``TRIM``, ``LEN``, ``LEFT``, ``RIGHT`` and ``REPLACE``, and the two date functions
``AGE`` and ``DT_FORMAT``.

Every function name is ALL CAPS, and a column name is a slug that must start with
a lowercase letter — so the two namespaces cannot overlap and no formula is ever
ambiguous about which one a name means.

Arithmetic (``+ - * /``) is numeric only — joining text is ``CONCAT``, so a
string can never slip into ``*`` and become a silent repetition. A value can be a
number or text depending on the formula; the column has no fixed output type.

A value is a number, text, or nothing — those three and no more. **Dates travel as
their ISO-8601 text**, which is what a `date` or `datetime` cell already stores, so
they need no fourth kind: ``AGE`` and ``DT_FORMAT`` read that text, and because ISO
sorts lexically, ``due > "2026-01-01"`` and ``MIN(start, end)``-by-comparison mean
what they look like they mean. A *multiple* choice cell — the one column value that
is neither scalar nor a reference — reads as its labels joined with ", ".

``AGE`` is the one function here that reads something outside the record: today's
date. That makes a stored answer go stale with the calendar rather than only with a
schema change — see `chain.py` on why the stored value is an index and
`maintenance/recompute_calculated.py` on the sweep that catches up with it. What is
on screen is always recomputed, so only a sort or filter can lag, and only by a day.

Missing data propagates as "no value": if any referenced cell is empty, the
result is ``None`` (the cell renders blank) rather than a silent zero or an empty
string — an incomplete row should read as incomplete.

``IS_NULL(x)`` is the one function that does not propagate it, and the only way
to ask about a blank at all: every other way of looking at one blanks the whole
formula, so ``IF(notes == "", …)`` can never be true. It answers ``1`` when its
argument has no value and ``0`` when it has one — which makes ``IF(IS_NULL(x),
0, x)`` the "treat blank as zero" a spreadsheet writes as IFNULL/COALESCE, and
``IS_NULL(a) + IS_NULL(b)`` a count of how many of them are missing.

This grammar is *row-scoped*: every name it resolves is one cell of one record.
List **metrics** layer a second, aggregate-level grammar on top of it
(``src/lists/metrics/aggregate.py``), where an aggregate's argument is one of
these row expressions — so the names marked public below (``ROW_FUNCTIONS``, the
three bounds, and the two ``*_row_expression`` seams) are that layer's interface
and are not free to change shape.
"""
import ast
import math
from datetime import date, datetime, timezone
from typing import Optional, Union

from src.core.exceptions import ValidationError

# Functions whose arguments and result are numbers. This set is a *gate*: a
# non-number argument blanks the cell rather than being coerced. `INT` is absent on
# purpose — reading a number out of text is the whole point of it.
_NUMERIC_FUNCTIONS = {"MIN", "MAX", "ROUND", "ABS"}
# Functions that operate on text. Numeric arguments are coerced to their text
# form (``CONCAT("$", price)``), and LEN's result is a number.
_STRING_FUNCTIONS = {"CONCAT", "UPPER", "LOWER", "TRIM", "LEN", "LEFT", "RIGHT", "REPLACE"}

# Function name -> (min_args, max_args). ``None`` means unbounded.
# Public: the metric language needs to know which names are row functions so it
# can tell them from its own aggregates.
ROW_FUNCTIONS: dict[str, tuple[int, Optional[int]]] = {
    "MIN": (1, None),
    "MAX": (1, None),
    "ROUND": (1, 2),
    "ABS": (1, 1),
    "INT": (1, 1),
    "IF": (3, 3),
    "IS_NULL": (1, 1),
    "CONCAT": (2, None),
    "UPPER": (1, 1),
    "LOWER": (1, 1),
    "TRIM": (1, 1),
    "LEN": (1, 1),
    "LEFT": (2, 2),
    "RIGHT": (2, 2),
    "REPLACE": (3, 3),
    "AGE": (1, 1),
    "DT_FORMAT": (2, 2),
}

_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_UNARYOPS = (ast.UAdd, ast.USub, ast.Not)
_CMPOPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)

# What evaluating a row expression may raise on data it cannot compute over —
# a division by zero, a number too large to represent. Public because both
# callers of the row evaluator (`evaluate` below, and the metric language, which
# runs one row at a time) have to swallow these per row, and because `IS_NULL`
# treats them as one more way of having no value.
ARITHMETIC_ERRORS = (ZeroDivisionError, ValueError, TypeError, OverflowError, MemoryError)

# A formula longer than this is almost certainly a mistake or an attempt to make
# parsing expensive; either way it is not something a person typed on purpose.
MAX_SOURCE = 1000
# Length is the wrong bound on its own: nesting is what costs, and a unary minus
# costs one character a level, so `----...-1` fits hundreds of levels inside the
# character budget and walking it overflows the stack. Depth is bounded directly.
# Nobody hand-writes a formula nested 32 deep.
MAX_DEPTH = 32
TOO_DEEP = f"A formula may not nest more than {MAX_DEPTH} levels deep"
# The ceiling on any text a formula produces. REPLACE can grow a string by a factor
# set by a literal, and nesting composes those factors while the source only grows
# linearly — so without this, a short formula allocates gigabytes, once per record,
# on every read. A calculated cell is a grid cell; nothing legitimate is this long.
_MAX_TEXT = 10_000

# How a *multiple* choice cell — a list of labels — reads as one value. The grid
# shows the same set with a comma, and this is the form a person would write by hand.
_MULTI_JOIN = ", "

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# Indexed by `date.weekday()`, which counts from Monday.
_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

# What `DT_FORMAT` understands, spelled the way `strftime` spells it — and rendered
# here rather than handed to `datetime.strftime`, for three reasons. An unknown
# directive is platform-dependent (glibc passes `%q` through, the Windows CRT
# raises), so the same formula would answer differently on a dev box and on the
# server. The name directives are *locale*-dependent, so the answer would depend on
# the environment the process happens to have been started in. And an explicit table
# is what gives the output a length bound to check before building it.
_DT_DIRECTIVES = {
    "Y": lambda d: f"{d.year:04d}",
    "y": lambda d: f"{d.year % 100:02d}",
    "m": lambda d: f"{d.month:02d}",
    "d": lambda d: f"{d.day:02d}",
    "j": lambda d: f"{d.timetuple().tm_yday:03d}",
    "H": lambda d: f"{d.hour:02d}",
    "I": lambda d: f"{(d.hour % 12) or 12:02d}",
    "M": lambda d: f"{d.minute:02d}",
    "S": lambda d: f"{d.second:02d}",
    "p": lambda d: "AM" if d.hour < 12 else "PM",
    "B": lambda d: _MONTHS[d.month - 1],
    "b": lambda d: _MONTHS[d.month - 1][:3],
    "A": lambda d: _WEEKDAYS[d.weekday()],
    "a": lambda d: _WEEKDAYS[d.weekday()][:3],
    "%": lambda d: "%",
}
# The widest any one directive expands to: "September". Every other one is shorter,
# and a year is four digits because `datetime` stops at 9999.
_DT_MAX_EXPANSION = 9


class Formula:
    """A parsed, validated formula and the column names it reads."""

    __slots__ = ("source", "tree", "refs")

    def __init__(self, source: str, tree: ast.Expression, refs: frozenset[str]):
        self.source = source
        self.tree = tree
        self.refs = refs


def parse(source: object) -> Formula:
    """Parse and fully validate a formula, or raise ``ValidationError``.

    Returns the parsed form and the set of column names it references, so the
    caller can check every one of them exists and is numeric.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValidationError("A calculated column requires a formula")
    if len(source) > MAX_SOURCE:
        raise ValidationError(f"A formula may be at most {MAX_SOURCE} characters")

    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValidationError(f"Formula is not a valid expression: {exc.msg}") from exc
    except RecursionError as exc:
        # The other half of the depth bound: nesting deep enough to overflow the
        # stack inside ast.parse itself, before `_check` ever gets to count it.
        raise ValidationError(TOO_DEEP) from exc

    refs: set[str] = set()
    _check(tree, refs, 0)
    return Formula(source, tree, frozenset(refs))


def _check(node: ast.AST, refs: set[str], depth: int) -> None:
    """Reject any node the language does not allow; collect column references.

    ``depth`` is how many levels down this node sits. The walk is top-down, so a
    formula nested past the limit is refused after `MAX_DEPTH` frames rather than
    when the stack runs out — and since `_eval` walks the same tree, bounding it
    here is what makes evaluating a stored formula safe.
    """
    if depth > MAX_DEPTH:
        raise ValidationError(TOO_DEEP)
    depth += 1

    if isinstance(node, ast.Expression):
        _check(node.body, refs, depth)

    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BINOPS):
            raise ValidationError("Only + - * / are allowed between values")
        _check(node.left, refs, depth)
        _check(node.right, refs, depth)

    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UNARYOPS):
            raise ValidationError("Unsupported unary operator in formula")
        _check(node.operand, refs, depth)

    elif isinstance(node, ast.BoolOp):  # and / or
        for value in node.values:
            _check(value, refs, depth)

    elif isinstance(node, ast.Compare):
        if not all(isinstance(op, _CMPOPS) for op in node.ops):
            raise ValidationError("Unsupported comparison in formula")
        _check(node.left, refs, depth)
        for comparator in node.comparators:
            _check(comparator, refs, depth)

    elif isinstance(node, ast.Call):
        if node.keywords or not isinstance(node.func, ast.Name):
            raise ValidationError("Formula functions take positional arguments only")
        name = node.func.id
        spec = ROW_FUNCTIONS.get(name)
        if spec is None:
            allowed = ", ".join(sorted(ROW_FUNCTIONS))
            raise ValidationError(
                f"Unknown function '{name}' in formula; allowed: {allowed}"
            )
        low, high = spec
        n = len(node.args)
        if n < low or (high is not None and n > high):
            want = f"{low}" if low == high else f"{low}+" if high is None else f"{low}-{high}"
            raise ValidationError(f"Function '{name}' takes {want} argument(s), got {n}")
        if name == "DT_FORMAT":
            # A format written out in the formula can be judged now, and should be:
            # the alternative is saving a column that answers blank on every record
            # for a reason nothing tells you. One that arrives from a cell cannot be
            # judged here and is checked per row instead (`_dt_format`).
            fmt = node.args[1]
            if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
                bad = _bad_directive(fmt.value)
                if bad is not None:
                    known = " ".join(f"%{key}" for key in _DT_DIRECTIVES)
                    raise ValidationError(
                        f"DT_FORMAT does not understand '{bad}'; it knows {known}"
                    )
        for arg in node.args:
            _check(arg, refs, depth)

    elif isinstance(node, ast.Name):
        # A bare name is a reference to another column. Function names are handled
        # above as Call.func and never reach here on their own.
        refs.add(node.id)

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


def column_refs(formula: Formula) -> frozenset[str]:
    """The column names this formula reads.

    Every name in ``refs`` is already a column: `_check` collects names in value
    position only and never descends into a call's *function* name, so a function
    never lands there in the first place. Position is what decides this, not
    spelling — the ALL-CAPS function names cannot collide with a lowercase column
    slug, but filtering by name would still be the wrong tool for the job.
    """
    return formula.refs


def check_row_expression(node: ast.AST, refs: set[str], depth: int) -> None:
    """Validate one node as a row expression, collecting the columns it reads.

    The seam the metric language builds on: an aggregate's argument is an
    ordinary row expression, so it is checked by this walk rather than a second
    copy of it. ``depth`` is threaded in from the *caller's* level so that
    nesting is bounded once across both grammars — an aggregate nested 30 deep
    does not get a fresh 32 levels for its argument.
    """
    _check(node, refs, depth)


def eval_row_expression(node: ast.AST, values: dict[str, object]):
    """Evaluate a validated row expression against one record's values.

    Raises the same arithmetic errors ``evaluate`` catches, so a caller running
    this once per record must catch them too (or let ``None`` stand for the row).
    """
    return _eval(node, values)


def evaluate(formula: Formula, values: dict[str, object]) -> Union[float, str, None]:
    """Compute the formula against a name->value map, or ``None``.

    The result is a number or a string depending on the formula. ``None`` on any
    missing operand, division by zero, or a non-finite result — a calculated cell
    is best-effort: one bad row must not fail a whole read.
    """
    try:
        result = _eval(formula.tree.body, values)
    except ARITHMETIC_ERRORS:
        return None
    return normalize_result(result)


def normalize_result(result: object) -> Union[float, str, None]:
    """A raw evaluation result as the one a caller may store or serialize.

    The rule for what a formula is allowed to *return* lives here rather than in
    each evaluator, so the metric language (which computes its result a
    different way) answers in the same shapes this one does.
    """
    if result is None:
        return None
    if isinstance(result, bool):
        # A whole-formula comparison (rare) reads as 1/0, which is what a
        # spreadsheet does and what a number column can store.
        return int(result)
    if isinstance(result, (int, float)):
        if isinstance(result, float) and not math.isfinite(result):
            return None
        return result
    if isinstance(result, str):
        return result
    return None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _to_text(v) -> str:
    """A value as text, the way a spreadsheet would show it: an integral float has
    no trailing ``.0``, so ``CONCAT("$", 3.0)`` is ``"$3"`` not ``"$3.0"``."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _to_int(v) -> Optional[int]:
    """A number or a numeric string as a whole number, truncated toward zero.

    A cast, not a floor: ``INT("-3.9")`` is ``-3``, the digits before the point,
    which is what reading text as a whole number means. Routing a string through
    ``float`` first is also what bounds this — anything past about 1.8e308 becomes
    infinity and answers ``None``, so a long run of digits can never be turned into
    an unbounded integer.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        number = v
    elif isinstance(v, str):
        try:
            number = float(v)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return math.trunc(number)


def _to_datetime(v) -> Optional[datetime]:
    """An ISO-8601 date or date-time string as a ``datetime``, or ``None``.

    A `date` cell reads as midnight, so a time directive in ``DT_FORMAT`` answers
    "00" rather than blanking the whole cell. Anything that is not a string is not a
    date, and gives no answer — this language's one way of saying so.

    Deliberately not `validation.parse_temporal`: that map is keyed by `ColumnType`
    and this has only a value, which may equally have come from a text column or
    another formula.
    """
    if not isinstance(v, str):
        return None
    try:
        # Accepts a bare "YYYY-MM-DD" as midnight, which is exactly what a `date`
        # cell holds — so one call covers both temporal column types.
        return datetime.fromisoformat(v)
    except (ValueError, TypeError):
        return None


def _today() -> date:
    """The date ``AGE`` counts up to.

    UTC rather than the acting user's zone, unlike a stamped ``today`` default
    (`validation._wall_clock_now`). `evaluate` is a pure function of one record's
    values — that is what lets a single parsed formula serve every row, and what
    lets the write path and the read path agree on an answer. Threading a user
    through would make the *stored index* depend on whoever last saved the record
    while a read recomputed it under whoever was reading, and the two would
    disagree about the same cell. The cost is being a day out for a reader far from
    UTC, on their birthday only.

    A seam as much as a helper: a test pins today by replacing it.
    """
    return datetime.now(timezone.utc).date()


def _age(v) -> Optional[int]:
    """Whole years from an ISO date to today, or ``None``.

    A birthday not yet reached this year counts one less, which is the whole reason
    this is a function rather than a subtraction of years. A date in the *future*
    gives a negative number rather than a blank: it is a real answer, and
    ``IF(AGE(d) < 0, …)`` can act on it, where a blank would be indistinguishable
    from an empty cell.
    """
    moment = _to_datetime(v)
    if moment is None:
        return None
    born, today = moment.date(), _today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _bad_directive(fmt: str) -> Optional[str]:
    """The first thing in ``fmt`` that ``DT_FORMAT`` does not understand, or ``None``.

    Shared by both moments a format can be judged: at parse time when it is written
    out in the formula, and per row when it arrives from a cell.
    """
    i = fmt.find("%")
    while i != -1:
        if i + 1 >= len(fmt):
            return "%"  # a trailing percent with nothing after it
        letter = fmt[i + 1]
        if letter not in _DT_DIRECTIVES:
            return f"%{letter}"
        i = fmt.find("%", i + 2)
    return None


def _dt_format(moment: datetime, fmt: str) -> Optional[str]:
    """``fmt`` with its date parts filled in, or ``None`` if it cannot be rendered."""
    if _bad_directive(fmt) is not None:
        return None
    # Measured before it is built, the way CONCAT and REPLACE are — measuring after
    # is too late, the allocation is the damage. `_DT_MAX_EXPANSION` is the ceiling
    # on what any one directive contributes, so this bounds the whole scan.
    if len(fmt) * _DT_MAX_EXPANSION > _MAX_TEXT:
        return None
    parts: list[str] = []
    i = 0
    while True:
        j = fmt.find("%", i)
        if j == -1:
            parts.append(fmt[i:])
            return "".join(parts)
        parts.append(fmt[i:j])
        # `_bad_directive` already proved this letter exists and is known.
        parts.append(_DT_DIRECTIVES[fmt[j + 1]](moment))
        i = j + 2


def _eval(node: ast.AST, values: dict[str, object]):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        v = values.get(node.id)
        if isinstance(v, bool):
            return None
        if isinstance(v, list):
            # The only list-valued column a formula may name is a `multiple` choice,
            # and its elements are labels — `validate_scalar` enforces that, and the
            # shape is already settled by `coerce_shape` before formulas run. Every
            # other type that stores a list (lookup, user, group) is not
            # referenceable, so this can never quietly stringify a row of uuids.
            # An empty selection is an empty cell, not an empty string.
            if not v or not all(isinstance(item, str) for item in v):
                return None
            return _MULTI_JOIN.join(v)
        return v if isinstance(v, (int, float, str)) else None

    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, values)
        if isinstance(node.op, ast.Not):
            return None if operand is None else (not operand)
        if operand is None:
            return None
        return +operand if isinstance(node.op, ast.UAdd) else -operand

    if isinstance(node, ast.BinOp):
        left = _eval(node.left, values)
        right = _eval(node.right, values)
        if left is None or right is None:
            return None
        # Arithmetic is numbers only: this is what stops "a" * 3 from becoming
        # "aaa" (Python's string repetition). Text is joined with CONCAT instead.
        if not (_is_num(left) and _is_num(right)):
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right  # Div; ZeroDivisionError caught in evaluate()

    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, values) for v in node.values]
        if any(v is None for v in vals):
            return None
        return all(vals) if isinstance(node.op, ast.And) else any(vals)

    if isinstance(node, ast.Compare):
        left = _eval(node.left, values)
        if left is None:
            return None
        result = True
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, values)
            if right is None:
                return None
            result = result and _compare(op, left, right)
            left = right
        return result

    if isinstance(node, ast.Call):
        name = node.func.id
        if name == "IF":
            cond = _eval(node.args[0], values)
            if cond is None:
                return None
            branch = node.args[1] if cond else node.args[2]
            return _eval(branch, values)
        if name == "IS_NULL":
            # Evaluated before the "any argument missing blanks the call" rule
            # below, because being missing is the whole question. Arithmetic
            # errors answer 1 for the same reason: `IS_NULL(total / qty)` asks
            # whether that expression has a value, and dividing by zero is one
            # more way of not having one.
            try:
                has_value = _eval(node.args[0], values) is not None
            except ARITHMETIC_ERRORS:
                return 1
            # 1/0 rather than True/False so the answer is a *number*: arithmetic
            # here is numbers only, and `IS_NULL(a) + IS_NULL(b)` — how many of
            # these are missing — should add up rather than blank the cell.
            return 0 if has_value else 1
        args = [_eval(a, values) for a in node.args]
        if any(a is None for a in args):
            return None

        if name in _NUMERIC_FUNCTIONS:
            # A non-number argument (e.g. a text column) blanks the cell rather
            # than comparing strings, which min/max would otherwise do.
            if not all(_is_num(a) for a in args):
                return None
            if name == "MIN":
                return min(args)
            if name == "MAX":
                return max(args)
            if name == "ABS":
                return abs(args[0])
            # ROUND(x) or ROUND(x, ndigits)
            ndigits = int(args[1]) if len(args) > 1 else None
            return round(args[0], ndigits)

        if name == "INT":
            # Outside `_NUMERIC_FUNCTIONS` on purpose: that set is a gate that blanks
            # on a non-number, and reading a number out of text is what this is for.
            return _to_int(args[0])

        if name == "AGE":
            return _age(args[0])

        if name == "DT_FORMAT":
            moment = _to_datetime(args[0])
            # The format is text and only text — a number in that position is not a
            # format anyone meant, so it blanks rather than being coerced the way a
            # string function's argument would be.
            if moment is None or not isinstance(args[1], str):
                return None
            return _dt_format(moment, args[1])

        # String functions. Numeric arguments are coerced to their text form, so
        # UPPER(price) or CONCAT("Qty ", qty) work as a spreadsheet user expects.
        # The two that can grow their input measure the result *before* building
        # it — measuring after is too late, the allocation is the damage — and
        # blank the cell when it would be oversized, the same "no value" every
        # other unanswerable formula gives. Capping each call also caps a nested
        # chain of them, since every level is bounded on its own.
        if name == "CONCAT":
            parts = [_to_text(a) for a in args]
            if sum(len(p) for p in parts) > _MAX_TEXT:
                return None
            return "".join(parts)
        if name == "UPPER":
            return _to_text(args[0]).upper()
        if name == "LOWER":
            return _to_text(args[0]).lower()
        if name == "TRIM":
            return _to_text(args[0]).strip()
        if name == "LEN":
            return len(_to_text(args[0]))
        if name == "REPLACE":
            text, old, new = (_to_text(a) for a in args)
            # `str.count` already answers this for an empty `old`, which inserts
            # between every character and at both ends: "abc".count("") is 4, and
            # "abc".replace("", "X") is indeed 3 + 4 characters long.
            if len(text) + text.count(old) * (len(new) - len(old)) > _MAX_TEXT:
                return None
            return text.replace(old, new)
        # LEFT/RIGHT: the first n characters, or the last n. A non-positive n is
        # the empty string (and n=0 must not fall through to Python's s[-0:] = s).
        text = _to_text(args[0])
        n = int(args[1])
        if n <= 0:
            return ""
        return text[:n] if name == "LEFT" else text[-n:]

    # Unreachable: parse() rejects every other node type up front.
    raise ValidationError("Unsupported expression in formula")


def _compare(op: ast.cmpop, left, right) -> bool:
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.Eq):
        return left == right
    return left != right  # NotEq
