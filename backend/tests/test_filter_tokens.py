"""`today`/`now` in a filter, resolved without a database.

The end-to-end behaviour is in `test_view_filtering.py`; this pins the resolution
rule itself, which those tests cannot: they run against whatever the clock says, so
they can prove that "today" is not the literal string but not *which* today it is.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.tables.columns.enums import ColumnType
from src.tables.columns.validation import filter_tokens, resolve_filter_token


def _user(tz):
    return SimpleNamespace(settings={"timezone": tz})


def test_each_type_knows_only_the_words_that_mean_something_to_it():
    # A date has no clock, so "now" is not a bound it can be given; a time has no
    # calendar. A datetime has both, which is why it takes both.
    assert filter_tokens(ColumnType.date) == ("today",)
    assert filter_tokens(ColumnType.time) == ("now",)
    assert set(filter_tokens(ColumnType.datetime)) == {"now", "today"}
    assert filter_tokens(ColumnType.text) == ()


def test_anything_else_is_passed_straight_through():
    # Including the same words against a column that does not accept them, which is
    # what keeps a `text` cell holding "today" comparing as that word.
    assert resolve_filter_token(ColumnType.text, "today") == "today"
    assert resolve_filter_token(ColumnType.date, "now") == "now"
    assert resolve_filter_token(ColumnType.date, "2026-01-01") == "2026-01-01"
    assert resolve_filter_token(ColumnType.number, 5) == 5
    assert resolve_filter_token(ColumnType.date, None) is None


def test_today_is_the_date_where_the_user_is():
    """Not the server's date -- the difference is a whole day for a third of them.

    Compared against two zones far enough apart that at every instant they disagree
    about something, rather than against a fixed expected date, which would only be
    right for part of the day.
    """
    east = resolve_filter_token(ColumnType.date, "today", _user("Pacific/Kiritimati"))
    west = resolve_filter_token(ColumnType.date, "today", _user("Pacific/Midway"))
    # +14 and -11: the calendar date differs between them at every moment.
    assert east > west

    utc_today = datetime.now(timezone.utc).date()
    assert resolve_filter_token(ColumnType.date, "today", _user("UTC")) == (
        utc_today.isoformat()
    )
    # No acting user, or one with no zone recorded, falls back to UTC rather than
    # refusing to answer -- the same fallback a stamped `today` default takes.
    assert resolve_filter_token(ColumnType.date, "today") == utc_today.isoformat()
    assert resolve_filter_token(
        ColumnType.date, "today", SimpleNamespace(settings=None)
    ) == utc_today.isoformat()


def test_a_datetime_resolves_today_to_midnight_and_now_to_the_reading():
    """The distinction the datetime column exists to make.

    `>= today` is the rest of the day and `>= now` is what is still to come, and the
    difference between them is everything that has already happened today.
    """
    today = resolve_filter_token(ColumnType.datetime, "today", _user("UTC"))
    now = resolve_filter_token(ColumnType.datetime, "now", _user("UTC"))
    assert today.endswith("T00:00:00")
    assert today <= now  # equal only in the first second of the day
    # Naive on the way out, matching how a datetime cell is stored: a bound carrying
    # an offset would raise against a typed value rather than compare with it.
    assert "+" not in now and not now.endswith("Z")

    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(datetime.fromisoformat(now) - utc_now) < timedelta(seconds=5)


def test_an_unknown_zone_does_not_stop_the_filter():
    """An id renamed or dropped between tzdata releases. Wrong by an offset beats a
    500 on every read of the view that names it."""
    assert resolve_filter_token(ColumnType.date, "today", _user("Europe/Atlantis")) == (
        datetime.now(timezone.utc).date().isoformat()
    )
