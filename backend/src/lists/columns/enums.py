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
# Types whose value is derived from other columns rather than typed by a person.
# The value is *stored* like any other cell — written from the formula on create and
# on every update — so it filters, sorts, searches and exports along with the rest;
# what makes these types special is only that the formula is the sole writer, so a
# client sending one is rejected.
COMPUTED_TYPES = frozenset({ColumnType.calculated})
# `user`/`group` are lookup with a fixed target — an account or a group, not a row.
PRINCIPAL_TYPES = frozenset({ColumnType.user, ColumnType.group})
