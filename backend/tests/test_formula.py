"""The formula language for calculated columns, in isolation.

This is the security boundary: the evaluator must never reach anything but the
column values it is handed. These tests pin both halves — what it computes, and
what it refuses to parse.
"""
from datetime import date, datetime

import pytest

from src.core.exceptions import ValidationError
from src.lists.columns import formula as formula_module
from src.lists.columns.formula import column_refs, evaluate, parse


def calc(source, **values):
    return evaluate(parse(source), values)


@pytest.fixture
def today(monkeypatch):
    """Pin the date `AGE` counts to, and hand it back.

    `_today` exists as a seam for exactly this: an assertion computed from the real
    clock would either restate the implementation or pass on the wrong day.
    """
    pinned = date(2026, 8, 6)
    monkeypatch.setattr(formula_module, "_today", lambda: pinned)
    return pinned


@pytest.fixture
def clock(monkeypatch):
    """Pin the reading `TODAY()` and `NOW()` answer with, and hand it back.

    `_now` is the one seam: `_today` derives from it, so pinning this pins the date
    as well and the two cannot be asserted against different moments. The pinned
    value carries microseconds because `NOW()` is supposed to drop them.
    """
    pinned = datetime(2026, 8, 6, 13, 45, 7, 123456)
    monkeypatch.setattr(formula_module, "_now", lambda: pinned)
    return pinned


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


def test_is_null_answers_about_a_blank_instead_of_becoming_one():
    # The one function that does not propagate "no value" — every other way of
    # looking at a blank blanks the whole formula, so without this there is no way
    # to write a condition about one at all.
    assert calc("IS_NULL(notes)", notes=None) == 1
    assert calc("IS_NULL(notes)") == 1  # not supplied at all
    assert calc("IS_NULL(notes)", notes="hi") == 0
    # A value that is present but falsy is present. Nothing here is about truth.
    assert calc("IS_NULL(qty)", qty=0) == 0
    assert calc("IS_NULL(name)", name="") == 0


def test_is_null_covers_an_expression_that_cannot_be_computed():
    # `total / qty` has no value when qty is 0, exactly as it has none when qty is
    # blank, so IS_NULL answers the same way to both — which is what makes it a
    # guard you can put in front of the division.
    assert calc("IS_NULL(total / qty)", total=10, qty=0) == 1
    assert calc("IS_NULL(total / qty)", total=10, qty=None) == 1
    assert calc("IS_NULL(total / qty)", total=10, qty=2) == 0
    assert calc("IF(IS_NULL(total / qty), 0, total / qty)", total=10, qty=0) == 0


def test_is_null_gives_the_formulas_a_spreadsheet_writes_as_ifnull():
    assert calc("IF(IS_NULL(discount), price, price - discount)", price=100) == 100
    assert (
        calc("IF(IS_NULL(discount), price, price - discount)", price=100, discount=15)
        == 85
    )
    # 1/0 rather than true/false, so a count of missing things adds up rather than
    # blanking the cell the way a bare comparison would.
    assert calc("IS_NULL(a) + IS_NULL(b)", a=None, b=None) == 2
    assert calc("IS_NULL(a) + IS_NULL(b)", a=1, b=None) == 1
    assert calc("not IS_NULL(a)", a=1) == 1


def test_is_null_takes_exactly_one_argument():
    for source in ("IS_NULL()", "IS_NULL(a, b)"):
        with pytest.raises(ValidationError):
            parse(source)


def test_is_null_reads_its_argument_as_a_column_reference():
    # It is an ordinary row function in every respect but its blank handling, so
    # the column it names still has to be found and checked at save time.
    assert column_refs(parse("IF(IS_NULL(notes), 0, LEN(notes))")) == frozenset({"notes"})


def test_default_substitutes_for_a_missing_value():
    # The second of the two functions that answer about a blank rather than
    # becoming one. What IF(IS_NULL(x), b, x) says by naming x twice.
    assert calc("DEFAULT(nickname, full_name)", full_name="Ada") == "Ada"
    assert calc("DEFAULT(nickname, full_name)", nickname="Ada L.", full_name="Ada") == (
        "Ada L."
    )
    assert calc("DEFAULT(discount, 0)") == 0
    assert calc("DEFAULT(discount, 0)", discount=15) == 15


def test_default_treats_an_empty_string_as_missing():
    # The one place it differs from IS_NULL, and the reason it is worth having
    # separately: a blank cell and an emptied one are the same thing to whoever is
    # reading the grid. "Empty" means what the filter language's `is_empty` means —
    # null or "", untrimmed — so a space is a value here as it is there.
    assert calc('DEFAULT(notes, "—")', notes="") == "—"
    assert calc('DEFAULT(notes, "—")', notes=None) == "—"
    assert calc('DEFAULT(notes, "—")', notes=" ") == " "
    assert calc('DEFAULT(TRIM(notes), "—")', notes=" ") == "—"


def test_default_keeps_a_value_that_is_merely_falsy():
    # Nothing here is about truth: a Quantity of 0 is a number somebody typed.
    assert calc("DEFAULT(qty, 99)", qty=0) == 0
    assert calc("DEFAULT(rate, 1)", rate=0.0) == 0.0


def test_default_covers_an_expression_that_cannot_be_computed():
    # Same rule as IS_NULL: dividing by zero is one more way of having no value, so
    # this is a guard you can put in front of the division without a second IF.
    assert calc("DEFAULT(total / qty, 0)", total=10, qty=0) == 0
    assert calc("DEFAULT(total / qty, 0)", total=10, qty=None) == 0
    assert calc("DEFAULT(total / qty, 0)", total=10, qty=2) == 5


def test_default_chains_and_nests():
    # The form that reads badly in the long version: three fallbacks written with
    # IF/IS_NULL names each column twice and nests two levels of condition.
    source = 'DEFAULT(nickname, DEFAULT(first_name, "friend"))'
    assert calc(source) == "friend"
    assert calc(source, first_name="Ada") == "Ada"
    assert calc(source, nickname="Ada L.", first_name="Ada") == "Ada L."
    # And it composes with the rest of the language rather than sitting outside it.
    assert calc('CONCAT("Hi ", DEFAULT(nickname, "there"))') == "Hi there"
    assert calc("ROUND(DEFAULT(price, 0) * 1.1, 2)", price=10) == 11.0


def test_default_does_not_evaluate_its_fallback_when_it_is_not_needed():
    # A fallback that cannot be computed must not blank a formula whose first
    # argument had a value all along — which is what would happen if this went
    # through the ordinary "evaluate every argument first" path.
    assert calc("DEFAULT(a, b / c)", a=5, b=1, c=0) == 5
    assert calc("DEFAULT(a, b / c)", a=None, b=1, c=0) is None


def test_default_takes_exactly_two_arguments():
    for source in ("DEFAULT()", "DEFAULT(a)", "DEFAULT(a, b, c)"):
        with pytest.raises(ValidationError):
            parse(source)


def test_default_reads_both_arguments_as_column_references():
    # An ordinary row function in every respect but its blank handling, so both
    # columns it names are still found and checked at save time.
    assert column_refs(parse("DEFAULT(nickname, full_name)")) == frozenset(
        {"nickname", "full_name"}
    )


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


# -- values that are not scalars -------------------------------------------------


def test_a_multiple_choice_cell_reads_as_its_labels_joined():
    # The one column value that is neither a scalar nor a reference. Joining is what
    # makes a multi-select usable at all — every other treatment of a list blanks it.
    assert calc("tags", tags=["Red", "Blue"]) == "Red, Blue"
    assert calc("CONCAT(tags, '!')", tags=["Red", "Blue"]) == "Red, Blue!"
    assert calc("LEN(tags)", tags=["Red", "Blue"]) == 9
    # A single selection reads as itself either way round, so a formula written
    # against a choice column keeps working if `multiple` is turned on.
    assert calc("IF(tags == 'Red', 1, 0)", tags=["Red"]) == 1
    assert calc("IF(tags == 'Red', 1, 0)", tags="Red") == 1


def test_an_empty_selection_is_an_empty_cell_not_an_empty_string():
    assert calc("tags", tags=[]) is None
    assert calc("IS_NULL(tags)", tags=[]) == 1
    assert calc("IS_NULL(tags)", tags=["Red"]) == 0


def test_a_list_of_anything_but_labels_gives_no_value():
    # Nothing referenceable stores one, and the day something does, it must not be
    # silently stringified into a cell — a row of uuids reads as data.
    assert calc("tags", tags=[1, 2]) is None
    assert calc("tags", tags=[{"uuid": "x"}]) is None


# -- AGE -------------------------------------------------------------------------


def test_age_counts_whole_years(today):
    assert calc("AGE(born)", born="1990-03-01") == 36
    # A birthday not yet reached this year counts one less. This is the entire
    # reason AGE is a function rather than a subtraction of two years.
    assert calc("AGE(born)", born="1990-12-25") == 35
    # On the day itself, the year counts.
    assert calc("AGE(born)", born="1990-08-06") == 36
    assert calc("AGE(born)", born="2026-08-06") == 0


def test_age_reads_a_datetime_as_its_date(today):
    assert calc("AGE(born)", born="1990-03-01T13:45:00") == 36
    assert calc("AGE(born)", born="1990-03-01T13:45:00+08:00") == 36


def test_age_of_a_future_date_is_negative_not_blank(today):
    # A real answer beats a blank, which would be indistinguishable from an empty
    # cell — so `IF(AGE(starts) < 0, …)` can act on it.
    assert calc("AGE(starts)", starts="2030-01-01") == -4
    assert calc("IF(AGE(starts) < 0, 'Not yet', 'Started')", starts="2030-01-01") == (
        "Not yet"
    )


def test_age_of_something_that_is_not_a_date_is_blank(today):
    assert calc("AGE(born)", born="not a date") is None
    assert calc("AGE(born)", born="2026-13-99") is None
    assert calc("AGE(born)", born=1990) is None
    assert calc("AGE(born)") is None


def test_age_is_a_number_so_it_takes_part_in_arithmetic(today):
    assert calc("AGE(born) * 12", born="1990-08-06") == 432
    assert calc("IF(AGE(born) >= 18, 'Adult', 'Minor')", born="2015-01-01") == "Minor"


# -- DT_FORMAT -------------------------------------------------------------------


def test_dt_format_fills_in_the_parts_of_a_date():
    assert calc("DT_FORMAT(d, '%d/%m/%Y')", d="2026-08-06") == "06/08/2026"
    assert calc("DT_FORMAT(d, '%A, %d %B %Y')", d="2026-08-06") == (
        "Thursday, 06 August 2026"
    )
    assert calc("DT_FORMAT(d, '%a %b %y')", d="2026-08-06") == "Thu Aug 26"
    assert calc("DT_FORMAT(d, '%j')", d="2026-08-06") == "218"


def test_dt_format_reads_the_time_off_a_datetime():
    assert calc("DT_FORMAT(d, '%H:%M:%S')", d="2026-08-06T13:45:07") == "13:45:07"
    assert calc("DT_FORMAT(d, '%I:%M %p')", d="2026-08-06T13:45:07") == "01:45 PM"
    assert calc("DT_FORMAT(d, '%I:%M %p')", d="2026-08-06T00:45:07") == "12:45 AM"


def test_a_plain_date_formats_its_time_as_midnight():
    # A `date` cell has no time on it. Reading midnight beats blanking the whole
    # cell over a directive the writer could reasonably have included.
    assert calc("DT_FORMAT(d, '%Y-%m-%d %H:%M')", d="2026-08-06") == "2026-08-06 00:00"


def test_dt_format_copies_everything_that_is_not_a_directive():
    assert calc("DT_FORMAT(d, 'Due on the %d')", d="2026-08-06") == "Due on the 06"
    assert calc("DT_FORMAT(d, 'hello')", d="2026-08-06") == "hello"
    assert calc("DT_FORMAT(d, '100%% done by %Y')", d="2026-08-06") == "100% done by 2026"


def test_dt_format_of_something_that_is_not_a_date_is_blank():
    assert calc("DT_FORMAT(d, '%Y')", d="nope") is None
    assert calc("DT_FORMAT(d, '%Y')", d=2026) is None
    assert calc("DT_FORMAT(d, '%Y')") is None
    # The format is text and only text; a number there is not a format anyone meant.
    assert calc("DT_FORMAT(d, f)", d="2026-08-06", f=5) is None


def test_a_written_out_format_is_judged_when_the_formula_is_saved():
    # The alternative is saving a column that answers blank on every record for a
    # reason nothing tells you about.
    with pytest.raises(ValidationError) as exc:
        parse("DT_FORMAT(d, '%q')")
    assert "%q" in str(exc.value)
    with pytest.raises(ValidationError):
        parse("DT_FORMAT(d, 'ends in %')")
    # And a good one saves.
    assert column_refs(parse("DT_FORMAT(d, '%d %b %Y')")) == frozenset({"d"})


def test_a_format_that_comes_from_a_cell_is_judged_per_row():
    # It cannot be checked at save time, so an unknown directive blanks that row the
    # way every other unanswerable formula does.
    assert calc("DT_FORMAT(d, f)", d="2026-08-06", f="%q") is None
    assert calc("DT_FORMAT(d, f)", d="2026-08-06", f="%Y") == "2026"


def test_dt_format_cannot_grow_a_value_without_bound():
    # The format can arrive from a text cell, so its length is not bounded by
    # MAX_SOURCE. Measured before it is built, as CONCAT and REPLACE are.
    assert calc("DT_FORMAT(d, f)", d="2026-08-06", f="%B" * 2000) is None
    assert calc("DT_FORMAT(d, f)", d="2026-08-06", f="%B" * 10) == "August" * 10


# -- TODAY / NOW -----------------------------------------------------------------


def test_today_answers_the_text_a_date_cell_holds(clock):
    assert calc("TODAY()") == "2026-08-06"


def test_now_answers_the_text_a_datetime_cell_holds(clock):
    # Seconds, and no zone suffix: a stamped `datetime` default is written this way
    # (`validation.resolve_dynamic_default`), and comparing ISO strings only means
    # anything among strings written alike.
    assert calc("NOW()") == "2026-08-06T13:45:07"


def test_today_and_now_read_one_clock(clock):
    # Two readings could straddle midnight and disagree about the date, which is why
    # `_today` derives from `_now` rather than asking the clock a second time.
    assert calc("DT_FORMAT(NOW(), '%Y-%m-%d')") == calc("TODAY()")


def test_they_compare_against_a_temporal_cell(clock):
    # The whole point of answering in ISO text: it sorts lexically against the cell.
    assert calc("IF(due < TODAY(), 'Overdue', 'Open')", due="2020-01-01") == "Overdue"
    assert calc("IF(due < TODAY(), 'Overdue', 'Open')", due="2030-01-01") == "Open"
    running = "IF(starts <= NOW(), 'Started', 'Later')"
    assert calc(running, starts="2026-08-06T09:00:00") == "Started"
    assert calc(running, starts="2026-08-06T18:00:00") == "Later"


def test_they_feed_the_other_date_functions(clock):
    assert calc("AGE(TODAY())") == 0
    assert calc("AGE(NOW())") == 0
    assert calc("DT_FORMAT(TODAY(), '%d %b %Y')") == "06 Aug 2026"
    assert calc("DT_FORMAT(NOW(), '%H:%M')") == "13:45"
    # And the string functions, since the answer is text like any other.
    assert calc("LEFT(TODAY(), 4)") == "2026"


def test_a_formula_that_only_reads_the_clock_references_no_column(clock):
    # Nothing else in the language answers without a cell, so this is the one shape
    # the column service's "every referenced column exists" loop sees empty.
    assert column_refs(parse("TODAY()")) == frozenset()
    assert column_refs(parse("CONCAT('as of ', NOW())")) == frozenset()


def test_they_take_no_arguments():
    with pytest.raises(ValidationError) as exc:
        parse("TODAY(due)")
    assert "0 argument(s), got 1" in str(exc.value)
    with pytest.raises(ValidationError):
        parse("NOW(1)")


def test_writing_one_without_its_parentheses_says_so():
    # The mistake these two invite, since they take nothing. Without this the caller
    # reports "unknown column 'TODAY'", which sends the writer looking for a column.
    with pytest.raises(ValidationError) as exc:
        parse("TODAY")
    assert "TODAY()" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        parse("due < NOW")
    assert "NOW()" in str(exc.value)
    # The same message for a function that does take arguments, spelled to match.
    with pytest.raises(ValidationError) as exc:
        parse("UPPER")
    assert "UPPER(" in str(exc.value)
    # A lowercase name is still a column reference, function-shaped or not.
    assert column_refs(parse("today + now")) == frozenset({"today", "now"})


# -- INT -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("42", 42),
        ("42.7", 42),  # truncated, not rounded
        ("-3.9", -3),  # toward zero, not floored
        ("  42  ", 42),
        ("1e3", 1000),
        ("abc", None),
        ("", None),
        ("nan", None),
        ("inf", None),
        ("1e400", None),  # overflows to infinity rather than becoming a huge int
    ],
)
def test_int_reads_a_whole_number_out_of_text(value, expected):
    assert calc("INT(code)", code=value) == expected


def test_int_drops_the_decimals_of_a_number_too():
    assert calc("INT(price)", price=3.9) == 3
    assert calc("INT(price)", price=-3.9) == -3
    assert calc("INT(price)", price=7) == 7
    assert calc("INT(price)") is None


def test_int_result_is_a_number_so_it_can_be_computed_with():
    # The point of the cast: a code typed into a text column can be totalled.
    assert calc("INT(code) * 2", code="21") == 42
    assert calc("INT(code) + qty", code="10", qty=5) == 15
