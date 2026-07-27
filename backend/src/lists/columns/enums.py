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
