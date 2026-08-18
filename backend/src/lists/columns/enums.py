import enum


class ColumnType(str, enum.Enum):
    # There is no separate `long_text`: one line versus several is how a text
    # column is *edited*, not what it stores, so it lives in config as
    # `text_mode` (see ColumnConfig). The old member is still a label on the
    # Postgres `column_type` enum — see the merge_long_text_into_text migration.
    text = "text"
    number = "number"
    checkbox = "checkbox"
    choice = "choice"
    lookup = "lookup"
    link = "link"
    email = "email"
    color = "color"
    rating = "rating"
    duration = "duration"
    user = "user"
    group = "group"
    date = "date"
    time = "time"
    datetime = "datetime"
    calculated = "calculated"


# Types whose value is a reference resolved against the database rather than a
# self-contained scalar: they cannot be checked by the pure validators, and they
# leave dangling ids behind when the thing they point at is deleted.
REFERENCE_TYPES = frozenset({ColumnType.lookup, ColumnType.user, ColumnType.group})
# Types that store no value of their own: the cell is derived from other columns
# at read time, never written. They are absent from a stored record, injected on
# the way out, and rejected if a client tries to set one.
COMPUTED_TYPES = frozenset({ColumnType.calculated})
# `user`/`group` are lookup with a fixed target — an account or a group, not a row.
PRINCIPAL_TYPES = frozenset({ColumnType.user, ColumnType.group})
# Types whose cell holds a JSON *number*, whatever it is a number of. A `rating` is
# a point on a scale and a `duration` is a count of seconds, but both are stored,
# compared, ordered and totalled exactly as `number` is — the difference between
# them is how they are typed in and drawn, which is the frontend's business.
#
# Every reader that has to know a cell is numeric tests this set rather than
# `== ColumnType.number`, and that is not tidiness: SQL comparison defaults to
# *text*, so a rating left out of it would put 10 before 9 in a sort and answer
# `> 3` by string order. `_numeric`, `_numeric_comparable` and `_order_terms` in
# `items/filtering.py` are the three that matter.
NUMERIC_TYPES = frozenset({
    ColumnType.number,
    ColumnType.rating,
    ColumnType.duration,
})
# What a formula in a `calculated` column may name. Every one of these stores a
# self-contained scalar a row expression can already read: a number, text, a choice
# *label* (a cell is the label itself — see `resolve_choice_options`), an ISO-8601
# temporal string, or another formula's answer. The rest are excluded for a reason
# rather than an omission: `checkbox` has no text form worth having, `link` is a URL
# nobody computes over, `color` is a rendering instruction rather than a value, and
# the REFERENCE_TYPES store ids that need a database round trip to become the label a
# person would expect to see. `email` is in because it is plain text with a checked
# grammar — "the address, or a fallback" and "the part after the @" are ordinary
# things to ask, and nothing about the check makes the string less readable.
FORMULA_REFERENCEABLE_TYPES = frozenset({
    ColumnType.number,
    ColumnType.rating,
    ColumnType.duration,
    ColumnType.text,
    ColumnType.email,
    ColumnType.calculated,
    ColumnType.choice,
    ColumnType.date,
    ColumnType.datetime,
})
