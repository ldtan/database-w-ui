"""A tiny, safe expression language for `calculated` columns.

A calculated column has no stored value. It carries a ``formula`` in its config
that references other columns of the same list **by name** (their slug, which is
already a valid Python identifier), and the value is computed at read time.

Safety is the whole point, so this does NOT use ``eval``. It parses the formula
to a Python AST once, refuses every node type it does not explicitly allow, and
then walks that tree itself. The only names it will resolve are the column values
handed to it; there is no path to a builtin, an attribute, or a subscript. A
malicious or mistaken formula can at worst return a wrong number or nothing.

Supported: the four arithmetic operators and parentheses, unary minus, numeric
**and** quoted-string literals, comparisons (for conditions), ``and``/``or``/``not``,
and the functions ``min``, ``max``, ``round``, ``abs``, ``IF(cond, then, else)``,
and the string functions ``CONCAT``, ``UPPER``, ``LOWER``, ``TRIM``, ``LEN``,
``LEFT``, ``RIGHT`` and ``REPLACE``.

Arithmetic (``+ - * /``) is numeric only — joining text is ``CONCAT``, so a
string can never slip into ``*`` and become a silent repetition. A value can be a
number or text depending on the formula; the column has no fixed output type.

Missing data propagates as "no value": if any referenced cell is empty, the
result is ``None`` (the cell renders blank) rather than a silent zero or an empty
string — an incomplete row should read as incomplete.
"""
import ast
import math
from typing import Optional, Union

from src.core.exceptions import ValidationError

# Functions whose arguments and result are numbers.
_NUMERIC_FUNCTIONS = {"min", "max", "round", "abs"}
# Functions that operate on text. Numeric arguments are coerced to their text
# form (``CONCAT("$", price)``), and LEN's result is a number.
_STRING_FUNCTIONS = {"CONCAT", "UPPER", "LOWER", "TRIM", "LEN", "LEFT", "RIGHT", "REPLACE"}

# Function name -> (min_args, max_args). ``None`` means unbounded.
_FUNCTIONS: dict[str, tuple[int, Optional[int]]] = {
    "min": (1, None),
    "max": (1, None),
    "round": (1, 2),
    "abs": (1, 1),
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
_MAX_SOURCE = 1000


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
    if len(source) > _MAX_SOURCE:
        raise ValidationError(f"A formula may be at most {_MAX_SOURCE} characters")

    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValidationError(f"Formula is not a valid expression: {exc.msg}") from exc

    refs: set[str] = set()
    _check(tree, refs)
    return Formula(source, tree, frozenset(refs))


def _check(node: ast.AST, refs: set[str]) -> None:
    """Reject any node the language does not allow; collect column references."""
    if isinstance(node, ast.Expression):
        _check(node.body, refs)

    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BINOPS):
            raise ValidationError("Only + - * / are allowed between values")
        _check(node.left, refs)
        _check(node.right, refs)

    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UNARYOPS):
            raise ValidationError("Unsupported unary operator in formula")
        _check(node.operand, refs)

    elif isinstance(node, ast.BoolOp):  # and / or
        for value in node.values:
            _check(value, refs)

    elif isinstance(node, ast.Compare):
        if not all(isinstance(op, _CMPOPS) for op in node.ops):
            raise ValidationError("Unsupported comparison in formula")
        _check(node.left, refs)
        for comparator in node.comparators:
            _check(comparator, refs)

    elif isinstance(node, ast.Call):
        if node.keywords or not isinstance(node.func, ast.Name):
            raise ValidationError("Formula functions take positional arguments only")
        name = node.func.id
        spec = _FUNCTIONS.get(name)
        if spec is None:
            allowed = ", ".join(sorted(_FUNCTIONS))
            raise ValidationError(
                f"Unknown function '{name}' in formula; allowed: {allowed}"
            )
        low, high = spec
        n = len(node.args)
        if n < low or (high is not None and n > high):
            want = f"{low}" if low == high else f"{low}+" if high is None else f"{low}-{high}"
            raise ValidationError(f"Function '{name}' takes {want} argument(s), got {n}")
        for arg in node.args:
            _check(arg, refs)

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


# Names that a formula references but that are also functions are functions, not
# columns — so the caller checks column existence against this set removed.
def column_refs(formula: Formula) -> frozenset[str]:
    """The referenced names that are columns (function names removed)."""
    return frozenset(formula.refs - set(_FUNCTIONS))


def evaluate(formula: Formula, values: dict[str, object]) -> Union[float, str, None]:
    """Compute the formula against a name->value map, or ``None``.

    The result is a number or a string depending on the formula. ``None`` on any
    missing operand, division by zero, or a non-finite result — a calculated cell
    is best-effort: one bad row must not fail a whole read.
    """
    try:
        result = _eval(formula.tree.body, values)
    except (ZeroDivisionError, ValueError, TypeError, OverflowError):
        return None
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
            if name == "min":
                return min(args)
            if name == "max":
                return max(args)
            if name == "abs":
                return abs(args[0])
            # round(x) or round(x, ndigits)
            ndigits = int(args[1]) if len(args) > 1 else None
            return round(args[0], ndigits)

        # String functions. Numeric arguments are coerced to their text form, so
        # UPPER(price) or CONCAT("Qty ", qty) work as a spreadsheet user expects.
        if name == "CONCAT":
            return "".join(_to_text(a) for a in args)
        if name == "UPPER":
            return _to_text(args[0]).upper()
        if name == "LOWER":
            return _to_text(args[0]).lower()
        if name == "TRIM":
            return _to_text(args[0]).strip()
        if name == "LEN":
            return len(_to_text(args[0]))
        if name == "REPLACE":
            return _to_text(args[0]).replace(_to_text(args[1]), _to_text(args[2]))
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
