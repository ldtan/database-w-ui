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
and the functions ``MIN``, ``MAX``, ``ROUND``, ``ABS``, ``IF(cond, then, else)``,
and the string functions ``CONCAT``, ``UPPER``, ``LOWER``, ``TRIM``, ``LEN``,
``LEFT``, ``RIGHT`` and ``REPLACE``.

Every function name is ALL CAPS, and a column name is a slug that must start with
a lowercase letter — so the two namespaces cannot overlap and no formula is ever
ambiguous about which one a name means.

Arithmetic (``+ - * /``) is numeric only — joining text is ``CONCAT``, so a
string can never slip into ``*`` and become a silent repetition. A value can be a
number or text depending on the formula; the column has no fixed output type.

Missing data propagates as "no value": if any referenced cell is empty, the
result is ``None`` (the cell renders blank) rather than a silent zero or an empty
string — an incomplete row should read as incomplete.

This grammar is *row-scoped*: every name it resolves is one cell of one record.
List **metrics** layer a second, aggregate-level grammar on top of it
(``src/lists/metrics/aggregate.py``), where an aggregate's argument is one of
these row expressions — so the names marked public below (``ROW_FUNCTIONS``, the
three bounds, and the two ``*_row_expression`` seams) are that layer's interface
and are not free to change shape.
"""
import ast
import math
from typing import Optional, Union

from src.core.exceptions import ValidationError

# Functions whose arguments and result are numbers.
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
    "IF": (3, 3),
    "CONCAT": (2, None),
    "UPPER": (1, 1),
    "LOWER": (1, 1),
    "TRIM": (1, 1),
    "LEN": (1, 1),
    "LEFT": (2, 2),
    "RIGHT": (2, 2),
    "REPLACE": (3, 3),
}

_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_UNARYOPS = (ast.UAdd, ast.USub, ast.Not)
_CMPOPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)

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
    except (ZeroDivisionError, ValueError, TypeError, OverflowError, MemoryError):
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


def _eval(node: ast.AST, values: dict[str, object]):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        v = values.get(node.id)
        if isinstance(v, bool):
            return None
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
