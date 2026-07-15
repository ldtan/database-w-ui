"""Pure (DB-free) validation helpers for column config and record values.

Shared by the column service (validating a column's own settings and default)
and the item service (validating record values), so the two never drift. Lookup
validation needs the database and stays in the item service.
"""
import re
from datetime import date, datetime, time
from typing import Optional
from urllib.parse import urlsplit

from src.core.exceptions import ValidationError

from .enums import ColumnType


# parser + human-readable expectation, per temporal type
_TEMPORAL = {
    ColumnType.date: (date.fromisoformat, "a date (YYYY-MM-DD)"),
    ColumnType.time: (time.fromisoformat, "a time (HH:MM[:SS])"),
    ColumnType.datetime: (datetime.fromisoformat, "a date-time (ISO-8601)"),
}
_NUMBER_FORMATS = {"plain", "currency", "percent"}

# A link cell is rendered as an anchor, so its scheme is a security boundary,
# not a preference: `javascript:`/`data:` URLs would be stored XSS. Columns may
# narrow this set via `allowed_schemes` but never widen it — whoever configures
# a column is an ordinary list editor, not a trusted admin.
SAFE_LINK_SCHEMES = ("http", "https", "mailto", "tel")
_DEFAULT_LINK_SCHEMES = ("http", "https")
# Schemes that address a host, so a bare "https:" with no site is meaningless.
_HOST_LINK_SCHEMES = ("http", "https")


def link_schemes(config: dict) -> list[str]:
    """The schemes a link column accepts, defaulting to http(s)."""
    schemes = config.get("allowed_schemes")
    return list(schemes) if schemes else list(_DEFAULT_LINK_SCHEMES)

# Dynamic tokens for a column's `default`/`onupdate`: values not fixed at config
# time but resolved per record when it is written. Temporal columns stamp the
# current date/time; a user column stamps whoever is making the write.
CURRENT_USER = "current_user"
_DYNAMIC_DEFAULTS = {
    ColumnType.date: {"today"},
    ColumnType.time: {"now"},
    ColumnType.datetime: {"now"},
    ColumnType.user: {CURRENT_USER},
}
# Types allowed an `onupdate`: a value re-stamped on create and on every edit. Its
# tokens are the same as the type's default — there is no static onupdate value.
ONUPDATE_TYPES = frozenset(_DYNAMIC_DEFAULTS)


def dynamic_tokens(column_type: ColumnType) -> set:
    """The dynamic default/onupdate tokens this column type accepts."""
    return _DYNAMIC_DEFAULTS.get(column_type, set())


def is_dynamic_default(column_type: ColumnType, default) -> bool:
    """True if ``default`` is a dynamic token ("today"/"now"/"current_user")."""
    return isinstance(default, str) and default in dynamic_tokens(column_type)


def resolve_dynamic_default(column_type: ColumnType, default, user=None):
    """Resolve a dynamic token to a concrete value; else pass the value through.

    Temporal tokens become an ISO string for *now*; ``current_user`` becomes the
    acting user's uuid (or None when there is no acting user to attribute it to)."""
    if not is_dynamic_default(column_type, default):
        return default
    if column_type == ColumnType.user:
        return str(user.uuid) if user is not None else None
    now = datetime.now()
    if column_type == ColumnType.date:
        return now.date().isoformat()
    if column_type == ColumnType.time:
        return now.time().replace(microsecond=0).isoformat()
    return now.replace(microsecond=0).isoformat()


def _expect(name: str, expected: str) -> None:
    raise ValidationError(f"Column '{name}' expects {expected}")


def parse_temporal(column_type: ColumnType, value):
    """Parse a temporal value; raises ValueError/TypeError if malformed."""
    parser, _ = _TEMPORAL[column_type]
    return parser(value)


def _slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "option"


def resolve_choice_options(config: dict, existing: Optional[dict]) -> list[dict]:
    """Return the canonical option list for a choice column.

    Explicit ``options`` win. Otherwise the ``choices`` shorthand ("A;B;C") is
    expanded, reusing an existing option's id for an unchanged label so records
    keep pointing at the right option across edits.
    """
    options = config.get("options")
    if options:
        return options

    choices = config.get("choices")
    if choices is None:
        # Neither `options` nor `choices` was resent: keep the existing options
        # (a config update shouldn't silently wipe the option set).
        return list((existing or {}).get("options", []) or [])

    existing_by_label = {
        opt["label"]: opt["id"] for opt in (existing or {}).get("options", []) or []
    }
    labels = [raw.strip() for raw in choices.split(";") if raw.strip()]
    # Reserve ids reused for unchanged labels up front, so a generated slug can
    # never collide with one assigned to a later label (e.g. "done" vs "Done").
    seen: set[str] = {
        existing_by_label[label] for label in labels if label in existing_by_label
    }
    result: list[dict] = []
    for label in labels:
        oid = existing_by_label.get(label)
        if oid is None:
            oid = base = _slugify(label)
            i = 2
            while oid in seen:
                oid = f"{base}_{i}"
                i += 1
            seen.add(oid)
        result.append({"id": oid, "label": label})
    return result


def validate_config_constraints(column_type: ColumnType, config: dict) -> None:
    """Validate that a column's own settings are well-formed (min <= max, etc.)."""
    if column_type in (ColumnType.text, ColumnType.long_text):
        for key in ("min_length", "max_length"):
            v = config.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                raise ValidationError(f"'{key}' must be a non-negative integer")
        lo, hi = config.get("min_length"), config.get("max_length")
        if lo is not None and hi is not None and lo > hi:
            raise ValidationError("'min_length' cannot exceed 'max_length'")
        pattern = config.get("pattern")
        if pattern is not None:
            try:
                re.compile(pattern)
            except re.error:
                raise ValidationError("'pattern' is not a valid regular expression")

    elif column_type == ColumnType.number:
        for key in ("min", "max"):
            v = config.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise ValidationError(f"number '{key}' must be a number")
        lo, hi = config.get("min"), config.get("max")
        if lo is not None and hi is not None and lo > hi:
            raise ValidationError("'min' cannot exceed 'max'")
        precision = config.get("precision")
        if precision is not None and (
            isinstance(precision, bool) or not isinstance(precision, int) or precision < 0
        ):
            raise ValidationError("'precision' must be a non-negative integer")
        fmt = config.get("number_format")
        if fmt is not None and fmt not in _NUMBER_FORMATS:
            raise ValidationError(
                f"'number_format' must be one of {sorted(_NUMBER_FORMATS)}"
            )

    elif column_type == ColumnType.link:
        schemes = config.get("allowed_schemes")
        if schemes is not None:
            if not isinstance(schemes, list) or not schemes:
                raise ValidationError(
                    "'allowed_schemes' must be a non-empty list of schemes"
                )
            unsafe = [s for s in schemes if s not in SAFE_LINK_SCHEMES]
            if unsafe:
                raise ValidationError(
                    f"Unsupported link scheme(s) {', '.join(map(str, unsafe))}; "
                    f"allowed: {', '.join(SAFE_LINK_SCHEMES)}"
                )
        max_length = config.get("max_length")
        if max_length is not None and (
            isinstance(max_length, bool)
            or not isinstance(max_length, int)
            or max_length < 1
        ):
            raise ValidationError("'max_length' must be a positive integer")

    elif column_type in _TEMPORAL:
        expected = _TEMPORAL[column_type][1]
        for key in ("min", "max"):
            v = config.get(key)
            if v is not None:
                try:
                    parse_temporal(column_type, v)
                except (ValueError, TypeError):
                    raise ValidationError(f"'{key}' must be {expected}")
        lo, hi = config.get("min"), config.get("max")
        if lo is not None and hi is not None:
            try:
                exceeds = parse_temporal(column_type, lo) > parse_temporal(column_type, hi)
            except TypeError:
                raise ValidationError(
                    "'min' and 'max' must both be timezone-aware or both naive"
                )
            if exceeds:
                raise ValidationError("'min' cannot exceed 'max'")


def validate_scalar(column_type: ColumnType, config: dict, name: str, value) -> None:
    """Validate a non-lookup value against its column type and constraints."""
    if column_type in (ColumnType.text, ColumnType.long_text):
        if not isinstance(value, str):
            _expect(name, "a string")
        lo, hi = config.get("min_length"), config.get("max_length")
        if lo is not None and len(value) < lo:
            raise ValidationError(f"Column '{name}' must be at least {lo} character(s)")
        if hi is not None and len(value) > hi:
            raise ValidationError(f"Column '{name}' must be at most {hi} character(s)")
        pattern = config.get("pattern")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            raise ValidationError(f"Column '{name}' does not match the required pattern")

    elif column_type == ColumnType.number:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _expect(name, "a number")
        if config.get("integer_only") and not (
            isinstance(value, int) or (isinstance(value, float) and value.is_integer())
        ):
            _expect(name, "an integer")
        lo, hi = config.get("min"), config.get("max")
        if lo is not None and value < lo:
            raise ValidationError(f"Column '{name}' must be >= {lo}")
        if hi is not None and value > hi:
            raise ValidationError(f"Column '{name}' must be <= {hi}")

    elif column_type == ColumnType.checkbox:
        if not isinstance(value, bool):
            _expect(name, "a boolean")

    elif column_type == ColumnType.choice:
        valid_ids = {opt["id"] for opt in config.get("options", []) or []}
        if config.get("multiple"):
            if not isinstance(value, list):
                _expect(name, "a list of option ids")
            candidates = value
        else:
            candidates = [value]
        for item in candidates:
            if item not in valid_ids:
                raise ValidationError(
                    f"'{item}' is not a valid option for column '{name}'"
                )

    elif column_type == ColumnType.link:
        if not isinstance(value, str):
            _expect(name, "a URL string")
        allowed = link_schemes(config)
        # urlsplit lowercases the scheme and strips the ASCII whitespace that
        # would otherwise smuggle "java\nscript:" past a naive prefix check.
        parts = urlsplit(value.strip())
        if parts.scheme == "":
            raise ValidationError(
                f"Column '{name}' must be a full URL including the scheme "
                f"(e.g. https://example.com)"
            )
        if parts.scheme not in allowed:
            raise ValidationError(
                f"Column '{name}' only accepts {', '.join(allowed)} links, "
                f"not '{parts.scheme}:'"
            )
        if parts.scheme in _HOST_LINK_SCHEMES and not parts.netloc:
            raise ValidationError(f"Column '{name}' must include a host")
        hi = config.get("max_length")
        if hi is not None and len(value) > hi:
            raise ValidationError(f"Column '{name}' must be at most {hi} character(s)")

    elif column_type in _TEMPORAL:
        if not isinstance(value, str):
            _expect(name, "an ISO-8601 string")
        try:
            parsed = parse_temporal(column_type, value)
        except (ValueError, TypeError):
            _expect(name, _TEMPORAL[column_type][1])
        lo, hi = config.get("min"), config.get("max")
        try:
            if lo is not None and parsed < parse_temporal(column_type, lo):
                raise ValidationError(f"Column '{name}' must be on or after {lo}")
            if hi is not None and parsed > parse_temporal(column_type, hi):
                raise ValidationError(f"Column '{name}' must be on or before {hi}")
        except TypeError:
            raise ValidationError(
                f"Column '{name}' must use the same timezone convention as its min/max"
            )
