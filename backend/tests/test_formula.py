"""The formula language for calculated columns, in isolation.

This is the security boundary: the evaluator must never reach anything but the
column values it is handed. These tests pin both halves — what it computes, and
what it refuses to parse.
"""
import pytest

from src.core.exceptions import ValidationError
from src.lists.columns.formula import column_refs, evaluate, parse


def calc(source, **values):
    return evaluate(parse(source), values)


def test_arithmetic_and_precedence():
    assert calc("qty * price", qty=3, price=10) == 30
    assert calc("(a + b) * c", a=1, b=2, c=4) == 12
    assert calc("-a + b", a=5, b=2) == -3
    assert calc("a / b", a=7, b=2) == 3.5


def test_functions():
    assert calc("round(a * b, 2)", a=3, b=1.005) == 3.02 or calc(
        "round(a, 1)", a=2.25
    ) in (2.2, 2.3)  # banker's rounding is fine; just exercise the path
    assert calc("min(a, b, c)", a=3, b=1, c=2) == 1
    assert calc("max(a, b)", a=3, b=9) == 9
    assert calc("abs(a - b)", a=2, b=5) == 3


def test_if_and_comparisons():
    assert calc("IF(qty > 10, price * 0.9, price)", qty=20, price=100) == 90
    assert calc("IF(qty > 10, price * 0.9, price)", qty=5, price=100) == 100
    assert calc("IF(a >= b, 1, 0)", a=2, b=2) == 1
    assert calc("IF(a == b, 1, 0)", a=2, b=3) == 0


def test_a_missing_operand_makes_the_result_blank_not_zero():
    # An incomplete row reads as incomplete, not as 0 * price.
    assert calc("qty * price", qty=None, price=10) is None
    assert calc("qty * price", price=10) is None  # not supplied at all
    # The untaken branch's blanks do not matter; only what is used.
    assert calc("IF(a > 0, b, c)", a=1, b=5, c=None) == 5
    assert calc("IF(a > 0, b, c)", a=-1, b=None, c=9) == 9


def test_division_by_zero_and_nonfinite_are_blank_not_errors():
    assert calc("a / b", a=1, b=0) is None
    assert calc("a / (b - c)", a=1, b=3, c=3) is None


def test_a_bare_comparison_reads_as_one_or_zero():
    assert calc("a > b", a=5, b=1) == 1
    assert calc("a > b", a=1, b=5) == 0


def test_column_refs_excludes_function_names():
    f = parse("round(min(qty, cap) * price, 2)")
    assert column_refs(f) == frozenset({"qty", "cap", "price"})


@pytest.mark.parametrize(
    "source",
    [
        '__import__("os").system("x")',  # no builtins
        "os.getcwd()",  # no attributes
        "data[0]",  # no indexing
        "foo(a)",  # unknown function
        "a ** b",  # power is not in the four operators
        "a % b",  # nor modulo
        "lambda: 1",  # no lambdas
        "[a for a in b]",  # no comprehensions
        '"a string"',  # no string literals
        "True",  # no boolean literals
        "IF(a, b)",  # IF needs exactly three arguments
        "round(a, b, c)",  # round takes at most two
        "",  # empty
        "   ",  # blank
        "a +",  # syntax error
    ],
)
def test_rejects_anything_outside_the_language(source):
    with pytest.raises(ValidationError):
        parse(source)


def test_a_formula_over_the_length_limit_is_rejected():
    with pytest.raises(ValidationError):
        parse("a" + " + a" * 500)
