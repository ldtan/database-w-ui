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
    assert calc("ROUND(a * b, 2)", a=3, b=1.005) == 3.02 or calc(
        "ROUND(a, 1)", a=2.25
    ) in (2.2, 2.3)  # banker's rounding is fine; just exercise the path
    assert calc("MIN(a, b, c)", a=3, b=1, c=2) == 1
    assert calc("MAX(a, b)", a=3, b=9) == 9
    assert calc("ABS(a - b)", a=2, b=5) == 3


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
    f = parse("ROUND(MIN(qty, cap) * price, 2)")
    assert column_refs(f) == frozenset({"qty", "cap", "price"})


def test_what_a_name_means_comes_from_its_position_not_its_spelling():
    # Function names are ALL CAPS and column slugs are lowercase, so these can no
    # longer collide — but refs are still collected by position, not by filtering
    # out anything spelled like a function. Filtering hid genuine columns from the
    # write-time checks (does it exist? is it a referenceable type?) while
    # `evaluate` resolved them anyway, so the distinction is worth pinning.
    f = parse("max * 2 + abs")
    assert column_refs(f) == frozenset({"max", "abs"})
    assert evaluate(f, {"max": 10, "abs": 5}) == 25
    # And a name in call position is still not a column.
    assert column_refs(parse("MIN(a, b)")) == frozenset({"a", "b"})


def test_the_lowercase_spelling_of_a_function_is_not_a_function():
    # `min(a, b)` is a call whose *function* name is a lowercase slug, and no such
    # function exists. It is refused outright rather than quietly read as a column.
    for source in ("min(a, b)", "round(a, 2)", "abs(a)", "max(a, b)"):
        with pytest.raises(ValidationError):
            parse(source)


def test_string_functions():
    assert calc("CONCAT(first, ' ', last)", first="John", last="Smith") == "John Smith"
    assert calc("UPPER(name)", name="abc") == "ABC"
    assert calc("LOWER(name)", name="ABC") == "abc"
    assert calc("TRIM(name)", name="  hi  ") == "hi"
    assert calc("LEN(name)", name="hello") == 5
    assert calc("LEFT(name, 3)", name="hello") == "hel"
    assert calc("RIGHT(name, 2)", name="hello") == "lo"
    assert calc("REPLACE(name, 'o', '0')", name="foobar") == "f00bar"


def test_left_and_right_with_a_non_positive_count_are_empty_not_the_whole_string():
    # n=0 must not fall through to Python's s[-0:] == s.
    assert calc("RIGHT(name, 0)", name="hello") == ""
    assert calc("LEFT(name, 0)", name="hello") == ""


def test_numbers_are_coerced_to_text_in_string_context():
    assert calc("CONCAT('Qty: ', qty)", qty=3) == "Qty: 3"
    # An integral float has no trailing .0.
    assert calc("CONCAT('$', price)", price=3.0) == "$3"


def test_arithmetic_stays_numeric_a_string_operand_blanks_it():
    # "ab" * 3 must not become "ababab".
    assert calc("name * 3", name="ab") is None
    assert calc("name + qty", name="ab", qty=1) is None


def test_string_comparison_in_a_condition():
    assert calc("IF(status == 'done', 1, 0)", status="done") == 1
    assert calc("IF(status == 'done', 1, 0)", status="open") == 0


def test_a_quoted_string_literal_is_allowed():
    assert calc("'hello'") == "hello"


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
        "True",  # no boolean literals
        "IF(a, b)",  # IF needs exactly three arguments
        "ROUND(a, b, c)",  # ROUND takes at most two
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


@pytest.mark.parametrize(
    "source",
    [
        "-" * 500 + "1",  # a unary minus costs one character a level
        "-" * 900 + "1",
        "-" * 999 + "1",  # exactly at the length limit, so length alone lets it by
        "not " * 200 + "a",
    ],
)
def test_a_deeply_nested_formula_is_rejected_not_a_crash(source):
    # Length is not a bound on depth: each of these is inside the character limit
    # and every one of them used to overflow the stack — either here, or later in
    # `evaluate`, which would take down every read of the list rather than one cell.
    assert len(source) <= 1000
    with pytest.raises(ValidationError):
        parse(source)


def test_nested_replace_cannot_grow_a_value_without_bound():
    # Each level multiplies the result while costing a fixed slice of the source,
    # so a short formula used to allocate megabytes — once per record, per read.
    source = "'a'"
    for _ in range(7):
        source = f"REPLACE({source}, 'a', 'aaaaaaaaaa')"
    assert len(source) < 200
    assert calc(source) is None


def test_an_oversized_text_result_is_blank_not_an_error():
    # A cap on each call caps a chain of them, and an oversized result reads as no
    # value — the same answer as any other formula that cannot produce one.
    assert calc("REPLACE(name, 'x', 'yy')", name="x" * 9000) is None
    assert calc("CONCAT(name, name)", name="x" * 9000) is None
    # Comfortably-sized ones are untouched.
    assert calc("REPLACE(name, 'x', 'yy')", name="x" * 10) == "y" * 20
    assert len(calc("CONCAT(name, name)", name="x" * 4000)) == 8000


def test_replace_measures_an_empty_search_string_correctly():
    # "abc".replace("", ".") inserts between every character and at both ends, so
    # the predicted size has to match: 3 + 4, not 3.
    assert calc("REPLACE(name, '', '.')", name="abc") == ".a.b.c."
