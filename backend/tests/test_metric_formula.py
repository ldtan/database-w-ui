"""The metric aggregate language, unit-tested without a database.

A metric formula is arithmetic over *aggregates*, and each aggregate's argument
is an ordinary row expression borrowed from `columns.formula`. These tests pin the
seam between those two levels — what each accepts, what a missing value does to a
total, and that nothing the row language refuses becomes reachable by wrapping it
in a SUM.
"""
import pytest

from src.core.exceptions import ValidationError
from src.lists.metrics import aggregate


def value_of(source, rows):
    """One formula's answer over `rows` (each a column-name -> value dict)."""
    formula = aggregate.parse(source)
    evaluator = aggregate.MetricEvaluator([formula])
    for row in rows:
        evaluator.add_row(row)
    return evaluator.result(formula)


ROWS = [
    {"qty": 3, "price": 10, "owner": "ana"},
    {"qty": 12, "price": 5, "owner": "raj"},
    {"qty": None, "price": 7, "owner": "ana"},
]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("SUM(qty)", 15),
        ("AVG(qty)", 7.5),
        ("MIN(qty)", 3),
        ("MAX(qty)", 12),
        ("MEDIAN(price)", 7),
        ("COUNT()", 3),
        ("COUNT(qty)", 2),
        ("EMPTY(qty)", 1),
        ("UNIQUE(owner)", 2),
    ],
)
def test_each_aggregate_answers_over_the_rows(source, expected):
    assert value_of(source, ROWS) == expected


def test_count_with_no_argument_counts_records_not_values():
    """COUNT() asks how many records there are, so a wholly empty one still counts;
    COUNT(col) asks how many have that value, so it does not."""
    rows = [{"qty": 1}, {"qty": None}, {}]
    assert value_of("COUNT()", rows) == 3
    assert value_of("COUNT(qty)", rows) == 1


def test_a_blank_is_skipped_rather_than_counted_as_zero():
    """The rule the whole app follows: an incomplete record reads as incomplete.
    Averaging over a blank as 0 would quietly halve this."""
    rows = [{"cost": 10}, {"cost": None}, {"cost": 20}]
    assert value_of("AVG(cost)", rows) == 15
    assert value_of("SUM(cost)", rows) == 30


@pytest.mark.parametrize(
    "source,expected",
    [
        ("SUM(qty)", 0),
        ("COUNT()", 0),
        ("COUNT(qty)", 0),
        ("EMPTY(qty)", 0),
        ("UNIQUE(qty)", 0),
        ("AVG(qty)", None),
        ("MIN(qty)", None),
        ("MAX(qty)", None),
        ("MEDIAN(qty)", None),
    ],
)
def test_over_no_records_only_the_totals_have_an_answer(source, expected):
    """A total of nothing is 0 and a count of nothing is 0, but an average or an
    extreme of nothing is not — those blank rather than invent a number."""
    assert value_of(source, []) == expected


def test_an_aggregates_argument_is_the_row_language():
    """`qty * price` is evaluated per record and only then summed, which is what
    makes SUM(qty * price) different from SUM(qty) * SUM(price)."""
    assert value_of("SUM(qty * price)", ROWS) == 90
    assert value_of("SUM(qty) * SUM(price)", ROWS) == 330


def test_arithmetic_composes_over_aggregate_results():
    assert value_of("SUM(qty) - SUM(price)", ROWS) == -7
    assert value_of("ROUND(SUM(qty) / COUNT(), 2)", ROWS) == 5.0


def test_a_comparison_inside_an_aggregate_counts_the_records_that_pass():
    """SUM over a condition is the spreadsheet idiom for "how many match", and it
    works because a bare comparison reads as 1/0."""
    assert value_of("SUM(qty > 5)", ROWS) == 1


def test_min_and_max_mean_the_aggregate_with_one_argument():
    """One argument is "the smallest of any record"; several is the row function
    picking between the values written there. Nothing is lost to the overlap,
    because a one-argument row MIN is just its argument."""
    assert value_of("MIN(qty)", ROWS) == 3
    assert value_of("MIN(SUM(qty), SUM(price))", ROWS) == 15
    assert value_of("MAX(SUM(qty), SUM(price))", ROWS) == 22


def test_text_is_not_part_of_a_total():
    """A total over a text column is not an error, it simply has nothing numeric to
    add — the same way the row language's numeric functions blank on a string."""
    rows = [{"owner": "ana"}, {"owner": "raj"}]
    assert value_of("SUM(owner)", rows) == 0
    assert value_of("COUNT(owner)", rows) == 2


def test_a_record_that_cannot_be_computed_blanks_only_itself():
    """One unanswerable record must not fail the whole metric."""
    rows = [{"a": 10, "b": 2}, {"a": 10, "b": 0}, {"a": 6, "b": 3}]
    assert value_of("SUM(a / b)", rows) == 7


def test_is_null_works_at_both_levels():
    """Inside an aggregate it asks about one record's cell; outside it asks about
    the aggregate's own result, which is how a card that would be blank gets a
    number instead."""
    assert value_of("SUM(IS_NULL(qty))", ROWS) == value_of("EMPTY(qty)", ROWS) == 1
    assert value_of("IS_NULL(AVG(qty))", []) == 1
    assert value_of("IF(IS_NULL(AVG(qty)), 0, AVG(qty))", []) == 0
    assert value_of("IF(IS_NULL(AVG(qty)), 0, AVG(qty))", ROWS) == 7.5


def test_is_null_sees_an_uncomputable_aggregate_expression_as_blank():
    """A metric over records none of which count divides by zero, and IS_NULL is
    the guard for it — so the error has to reach IS_NULL as a blank rather than
    blanking the whole card on its way past."""
    rows = [{"a": 5, "owner": None}, {"a": 5, "owner": None}]
    assert value_of("SUM(a) / COUNT(owner)", rows) is None
    assert value_of("IS_NULL(SUM(a) / COUNT(owner))", rows) == 1
    assert value_of("IF(IS_NULL(SUM(a) / COUNT(owner)), 0, SUM(a) / COUNT(owner))", rows) == 0


def test_default_works_at_both_levels_too():
    """It came with the row language and reaches the aggregate one for free.

    Outside an aggregate it is the short way to put a number on a card that would
    otherwise be blank; inside one it fills a gap per record before the total is
    taken, which is a different answer and sometimes the wanted one.
    """
    assert value_of("DEFAULT(AVG(qty), 0)", []) == 0
    assert value_of("DEFAULT(AVG(qty), 0)", ROWS) == 7.5
    assert value_of("DEFAULT(SUM(a) / COUNT(owner), 0)", [{"a": 5, "owner": None}]) == 0
    # Per record: the blank counts as zero in the total rather than being skipped,
    # so this differs from SUM(qty) — which is the reason to write it.
    rows = [{"qty": 4}, {"qty": None}]
    assert value_of("AVG(DEFAULT(qty, 0))", rows) == 2
    assert value_of("AVG(qty)", rows) == 4


def test_a_bare_column_name_is_refused():
    """The rule that makes the two levels unambiguous: `hours` is one value per
    record, and a metric is one value in total."""
    with pytest.raises(ValidationError, match="Wrap 'qty' in an aggregate"):
        aggregate.parse("qty")
    with pytest.raises(ValidationError, match="Wrap 'price' in an aggregate"):
        aggregate.parse("SUM(qty) + price")


def test_a_formula_with_no_aggregate_is_refused():
    with pytest.raises(ValidationError, match="at least one aggregate"):
        aggregate.parse("1 + 1")


def test_aggregates_do_not_nest():
    """Inside an aggregate the grammar is the row language, which has no SUM — so
    SUM(SUM(x)) is refused rather than silently meaning something."""
    with pytest.raises(ValidationError, match="Unknown function 'SUM'"):
        aggregate.parse("SUM(SUM(qty))")


def test_wrong_arity_is_refused():
    with pytest.raises(ValidationError, match="Aggregate 'SUM' takes 1"):
        aggregate.parse("SUM()")
    with pytest.raises(ValidationError, match="Aggregate 'COUNT' takes 0-1"):
        aggregate.parse("COUNT(qty, price)")


@pytest.mark.parametrize(
    "source",
    [
        "SUM(qty).real",
        "SUM(qty)[0]",
        "__import__('os')",
        "SUM(qty.__class__)",
        "COUNT(lambda: 1)",
    ],
)
def test_there_is_no_escape_hatch(source):
    """Everything the row language refuses stays refused here: wrapping an
    expression in an aggregate must not be a way around the allow-list."""
    with pytest.raises(ValidationError):
        aggregate.parse(source)


def test_the_row_languages_bounds_still_apply():
    """Length and depth are bounded once across both levels — an aggregate does not
    hand its argument a fresh allowance."""
    with pytest.raises(ValidationError, match="at most"):
        aggregate.parse("SUM(qty)" + " + SUM(qty)" * 200)
    with pytest.raises(ValidationError, match="nest more than"):
        aggregate.parse("SUM(" + "-" * 40 + "qty)")


def test_column_refs_reports_the_names_the_formula_reads():
    formula = aggregate.parse("SUM(qty * price) / COUNT(owner)")
    assert aggregate.column_refs(formula) == {"qty", "price", "owner"}
    # A function name is never mistaken for a column, at either level.
    assert aggregate.column_refs(aggregate.parse("ROUND(AVG(qty), 1)")) == {"qty"}


def test_one_pass_answers_every_metric_over_the_same_records():
    """Several metrics share a scan, which is what lets a list be read once
    however many cards it carries — and is why they always agree with each other."""
    formulas = [aggregate.parse(s) for s in ("SUM(qty)", "COUNT()", "AVG(price)")]
    evaluator = aggregate.MetricEvaluator(formulas)
    assert evaluator.column_names == {"qty", "price"}
    for row in ROWS:
        evaluator.add_row(row)
    assert [evaluator.result(f) for f in formulas] == [15, 3, pytest.approx(7.3333, rel=1e-3)]
