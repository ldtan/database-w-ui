"""Pure (DB-free) validation helpers for column config and record values.

Shared by the column service (validating a column's own settings and default)
and the record service (validating record values), so the two never drift. Lookup
validation needs the database and stays in the record service.
"""
import re
from datetime import date, datetime, time, timezone
from typing import Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from src.core.exceptions import ValidationError

from .enums import ColumnType


# parser + human-readable expectation, per temporal type
_TEMPORAL = {
    ColumnType.date: (date.fromisoformat, "a date (YYYY-MM-DD)"),
    ColumnType.time: (time.fromisoformat, "a time (HH:MM[:SS])"),
    ColumnType.datetime: (datetime.fromisoformat, "a date-time (ISO-8601)"),
}
_NUMBER_FORMATS = {"plain", "currency", "percent"}
_RATING_ICONS = {"star", "heart", "number"}
_DURATION_FORMATS = {"h:mm", "h:mm:ss", "m:ss", "decimal_hours"}
# A scale nobody can read is not a scale. Ten glyphs is already past the point
# where somebody counts rather than sees, and the cap is what keeps a rating a
# rating rather than a `number` column drawn very badly.
RATING_MAX = 10
_DEFAULT_RATING_MAX = 5


def rating_scale(config: dict) -> int:
    """The top of a rating column's scale, defaulting to five stars."""
    top = config.get("max")
    if isinstance(top, bool) or not isinstance(top, int):
        return _DEFAULT_RATING_MAX
    return top


_SECONDS_RE = re.compile(r"\d+(?:\.\d+)?\Z")
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}
_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([hms])")
_UNIT_FORM_RE = re.compile(r"(?:\d+(?:\.\d+)?\s*[hms]\s*)+\Z")


def parse_duration(config: dict, text: str) -> Optional[float]:
    """Read a written duration as seconds, or None if it is not one.

    A cell stores seconds, and the export writes those seconds plainly — so a bare
    number here **always** means seconds, whatever the column is formatted as. That
    is what makes an exported file import back unchanged, and it is the one rule in
    this grammar that is not negotiable.

    On top of that, two written forms people actually type:

    - **Colon form.** `1:30:00` is always h:mm:ss. `1:30` is h:mm — an hour and a
      half — *unless* the column is formatted `m:ss`, where it reads as a minute and
      a half, because that is what the column has been showing them. The parts after
      the first must be under 60, so `1:75` is a typo rather than a silent 2:15.
    - **Unit form.** `90m`, `1.5h`, `1h30m`, `45s`. Unambiguous whatever the format,
      and the only way to write a duration that is right in any column.
    """
    raw = text.strip().lower()
    if not raw:
        return None
    if _SECONDS_RE.fullmatch(raw):
        return float(raw)
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) > 3 or not all(_SECONDS_RE.fullmatch(p) for p in parts):
            return None
        values = [float(p) for p in parts]
        if any(v >= 60 for v in values[1:]):
            return None
        if len(parts) == 3:
            hours, minutes, seconds = values
        elif config.get("duration_format") == "m:ss":
            hours, (minutes, seconds) = 0.0, values
        else:
            (hours, minutes), seconds = values, 0.0
        return hours * 3600 + minutes * 60 + seconds
    if _UNIT_FORM_RE.fullmatch(raw):
        return sum(
            float(amount) * _UNIT_SECONDS[unit]
            for amount, unit in _UNIT_RE.findall(raw)
        )
    return None
# How a text column is edited. Unset means "single"; see ColumnConfig.text_mode.
# "rich" stores HTML rather than plain text — see columns/richtext.py.
TEXT_MODES = ("single", "multiline", "rich")

# A link cell is rendered as an anchor, so its scheme is a security boundary,
# not a preference: `javascript:`/`data:` URLs would be stored XSS. Columns may
# narrow this set via `allowed_schemes` but never widen it — whoever configures
# a column is an ordinary table editor, not a trusted admin.
SAFE_LINK_SCHEMES = ("http", "https", "mailto", "tel")
_DEFAULT_LINK_SCHEMES = ("http", "https")
# Schemes that address a host, so a bare "https:" with no site is meaningless.
_HOST_LINK_SCHEMES = ("http", "https")


def link_schemes(config: dict) -> list[str]:
    """The schemes a link column accepts, defaulting to http(s)."""
    schemes = config.get("allowed_schemes")
    return list(schemes) if schemes else list(_DEFAULT_LINK_SCHEMES)


# Deliberately not RFC 5322. That grammar admits quoted local parts, comments and
# bare-host domains that no mail provider on earth accepts, so implementing it
# faithfully rejects nothing anybody would type wrong while passing plenty nobody
# can send to. This is the check a signup form makes: one `@`, something either
# side of it, a dot in the host, and no whitespace anywhere.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
# `#rrggbb`. Three-digit shorthand is expanded before this is applied rather than
# accepted alongside it, so `#abc` and `#aabbcc` cannot be two values for one
# colour — which is what `unique`, filtering and grouping would otherwise see.
_COLOR_RE = re.compile(r"#[0-9a-f]{6}\Z")
_COLOR_SHORTHAND_RE = re.compile(r"#(?P<r>[0-9a-f])(?P<g>[0-9a-f])(?P<b>[0-9a-f])\Z")


def normalize_email(value: str) -> str:
    """An email address as it is stored: trimmed and lowercased.

    Lowercased in full, host *and* local part. The local part is technically
    case-sensitive per RFC 5321, and treating it that way is the wrong trade here:
    no provider in practice distinguishes `Ada@` from `ada@`, while storing both
    means `unique` does not catch the duplicate it exists to catch, a filter for
    one misses the other, and grouping shows the same person twice.
    """
    return value.strip().lower()


_PHONE_FORMATS = {"e164", "national", "international"}
_MAP_LINKS = {"osm", "google", "apple"}
# The only keys a location cell may hold. Fixed rather than open so a cell cannot
# quietly become a place to stash arbitrary JSON — `data` is schemaless, and this
# type is the first one whose *value* is an object.
LOCATION_KEYS = ("address", "lat", "lng")
_ADDRESS_MAX = 500
# The coordinate suffix of the one-line text form. Anchored to the end and required
# to *look like* a pair of coordinates, which is what lets an address keep its own
# "@": "Unit 3 @ The Mall" has no numeric suffix, so all of it is the address, while
# "Unit 3 @ The Mall @10.0,123.0" splits at the last one. A leading space is written
# but not required to read, so a hand-typed "@10.3,123.8" works too.
_COORD_SUFFIX_RE = re.compile(
    r"\s*@(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\Z"
)


def format_location(value) -> Optional[str]:
    """A location as one line of text — the form the export writes.

    `12 Rizal St, Cebu City @10.31570,123.88540`, either half optional. This is the
    canonical text form in both directions: what a CSV carries, and what a
    conversion to `text` produces, so `parse_location` reads it straight back.

    Five decimal places is about a metre, which is finer than any address is, and
    keeps the file readable — a float repr of `10.315700000000001` in a spreadsheet
    cell helps nobody.
    """
    if not isinstance(value, dict):
        return None
    address = value.get("address") or ""
    lat, lng = value.get("lat"), value.get("lng")
    if lat is None or lng is None:
        return address or None
    coords = f"@{float(lat):.5f},{float(lng):.5f}"
    return f"{address} {coords}" if address else coords


def parse_location(text: str) -> Optional[dict]:
    """Read the one-line text form back into a cell, or None if it is empty.

    Anything that is not the coordinate suffix is the address, so a line a person
    typed by hand — just an address, no coordinates — is read as one rather than
    refused. Only a *well-formed* suffix is taken as coordinates; a trailing "@"
    that is part of the address stays part of it.
    """
    raw = text.strip()
    if not raw:
        return None

    address, lat, lng = raw, None, None
    suffix = _COORD_SUFFIX_RE.search(raw)
    if suffix:
        lat, lng = float(suffix.group(1)), float(suffix.group(2))
        address = raw[: suffix.start()].strip()

    return {"address": address or None, "lat": lat, "lng": lng}


def _coordinate(config: dict, name: str, value) -> None:
    """Check one {lat, lng} pair, wherever it appears (a cell, a default centre)."""
    lat, lng = value.get("lat"), value.get("lng")
    if (lat is None) != (lng is None):
        raise ValidationError(
            f"Column '{name}' needs both a latitude and a longitude, or neither"
        )
    if lat is None:
        return
    for label, coord, limit in (("latitude", lat, 90), ("longitude", lng, 180)):
        if isinstance(coord, bool) or not isinstance(coord, (int, float)):
            raise ValidationError(f"Column '{name}': {label} must be a number")
        if not -limit <= coord <= limit:
            raise ValidationError(
                f"Column '{name}': {label} must be between -{limit} and {limit}"
            )


def phone_regions(config: dict) -> tuple[Optional[str], list[str]]:
    """A phone column's default region and its allowed set, both normalised upper."""
    default = config.get("default_region")
    default = default.strip().upper() if isinstance(default, str) and default else None
    allowed = config.get("allowed_regions") or []
    allowed = [r.strip().upper() for r in allowed if isinstance(r, str) and r.strip()]
    return default, allowed


def normalize_phone(config: dict, name: str, value: str) -> str:
    """A phone number in the form the column stores: E.164.

    **The normalisation is the type.** A regex could tell `+639171234567` from
    `banana`, but it could not tell that `0917 123 4567`, `+63 917 123 4567` and
    `09171234567` are one number — so `unique` would miss the duplicate it exists to
    catch, a filter for one spelling would miss the others, and grouping would show
    one person twice. Every one of those becomes `+639171234567` here, on the write
    path every caller shares.

    Raises `ValidationError` with the column's name, in the house style, so a bad
    number reads like every other refused cell rather than like a library error.
    """
    import phonenumbers

    default_region, allowed = phone_regions(config)
    raw = value.strip()
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        # The commonest cause by far, and worth saying outright: a national number
        # on a column that was never told which country it is national to.
        if default_region is None and not raw.startswith("+"):
            raise ValidationError(
                f"Column '{name}' needs the country code (e.g. +639171234567), "
                f"or a default region set on the column"
            )
        raise ValidationError(f"Column '{name}': '{value}' is not a phone number")

    if not phonenumbers.is_valid_number(parsed):
        # Distinct from a parse failure: this is well-formed and still not a number
        # anyone could dial — the right length for no operator in that country.
        raise ValidationError(
            f"Column '{name}': '{value}' is not a valid phone number"
        )

    if allowed:
        region = phonenumbers.region_code_for_number(parsed)
        if region not in allowed:
            raise ValidationError(
                f"Column '{name}' only accepts numbers from "
                f"{', '.join(allowed)}, not {region or 'that country'}"
            )

    return phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164
    )


def normalize_color(value: str) -> str:
    """A colour as it is stored: `#rrggbb`, lowercased, shorthand expanded."""
    text = value.strip().lower()
    short = _COLOR_SHORTHAND_RE.fullmatch(text)
    if short:
        r, g, b = short.group("r"), short.group("g"), short.group("b")
        return f"#{r}{r}{g}{g}{b}{b}"
    return text

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


# The same idea one step along: a value a *filter* may be written against, resolved
# when the query runs rather than when the condition was saved. "Due on or after
# today" is a question whose answer has to change overnight, and a stored ISO string
# cannot express it — a view saved in January still filters on January in March.
#
# Deliberately a second table rather than a reuse of the one above, because the two
# vocabularies differ in one place: a `datetime` column accepts both, where "today"
# means this morning's midnight. A default cannot mean that (stamping a datetime
# cell with midnight is not what anybody asking for "now" wanted), but a *bound* on
# one reads perfectly — `due >= today` is every appointment left in the day.
_FILTER_TOKENS = {
    ColumnType.date: ("today",),
    ColumnType.time: ("now",),
    ColumnType.datetime: ("now", "today"),
}


def dynamic_tokens(column_type: ColumnType) -> set:
    """The dynamic default/onupdate tokens this column type accepts."""
    return _DYNAMIC_DEFAULTS.get(column_type, set())


def filter_tokens(column_type: ColumnType) -> tuple[str, ...]:
    """The dynamic values a filter on this column type may compare against."""
    return _FILTER_TOKENS.get(column_type, ())


def resolve_filter_token(column_type: ColumnType, value, user=None):
    """Resolve a filter's "today"/"now" to an ISO string; else pass ``value`` through.

    The clock is the acting user's, for the reason `_wall_clock_now` gives: these
    columns hold a wall-clock reading, so "today" has to mean the date on the
    calendar the rest of the row was typed against. A filter differs from a default
    only in that nothing is written down — the comparison happens and is forgotten —
    so the answer is allowed to depend on who is asking, which is exactly what makes
    one saved view read correctly for a team spread across timezones.

    "today" on a `datetime` column resolves to midnight, so `>= today` is the rest of
    the day and `< today` is everything before it. That is the only resolution that
    composes with the text comparison the filter actually performs: an ISO date-time
    string sorts lexically, and every reading of today is at or after its midnight.
    """
    if not isinstance(value, str) or value not in filter_tokens(column_type):
        return value
    now = _wall_clock_now(user)
    if column_type == ColumnType.date:
        return now.date().isoformat()
    if column_type == ColumnType.time:
        return now.time().replace(microsecond=0).isoformat()
    if value == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return now.replace(microsecond=0).isoformat()


def is_dynamic_default(column_type: ColumnType, default) -> bool:
    """True if ``default`` is a dynamic token ("today"/"now"/"current_user")."""
    return isinstance(default, str) and default in dynamic_tokens(column_type)


def _wall_clock_now(user) -> datetime:
    """The current wall-clock reading where the acting user is.

    Their zone rather than the process's: the server runs on UTC, so stamping
    `today` for someone in UTC+8 writes yesterday's date through the first eight
    hours of their day — disagreeing with the calendar they typed every other cell
    in the row against, and with the date the picker would have offered them.

    Naive on the way out, because these columns hold a wall-clock value and not an
    instant (see frontend/src/lib/temporal.ts). A stamped value has to be
    indistinguishable from a typed one, or the two sort against each other wrongly
    and min/max comparison raises over the mixed convention.

    Falls back to UTC when nobody is acting, no zone has been recorded yet, or the
    stored id is not one this build's tz database knows — an id that was renamed or
    dropped between tzdata releases. Wrong by an offset beats refusing the write.
    """
    # getattr covers both "no acting user" and a user whose blob is empty.
    name = (getattr(user, "settings", None) or {}).get("timezone")
    try:
        tz = ZoneInfo(name) if name else timezone.utc
    except (KeyError, ValueError):
        # ZoneInfoNotFoundError (a KeyError) for an unknown id; ValueError for one
        # that is not a well-formed key at all.
        tz = timezone.utc
    return datetime.now(tz).replace(tzinfo=None)


def resolve_dynamic_default(column_type: ColumnType, default, user=None):
    """Resolve a dynamic token to a concrete value; else pass the value through.

    Temporal tokens become an ISO string for *now* in the acting user's timezone
    (see _wall_clock_now); ``current_user`` becomes the acting user's uuid (or None
    when there is no acting user to attribute it to)."""
    if not is_dynamic_default(column_type, default):
        return default
    if column_type == ColumnType.user:
        return str(user.uuid) if user is not None else None
    now = _wall_clock_now(user)
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


def resolve_choice_options(config: dict, existing: Optional[dict]) -> list[str]:
    """Return the canonical option table for a choice column: plain labels.

    Explicit ``options`` win. Otherwise the ``choices`` shorthand ("A;B;C") is
    split. A cell stores the label itself, so there is nothing to keep stable
    here — an option that changes spelling is simply a different option.
    """
    options = config.get("options")
    if options:
        return [str(label).strip() for label in options if str(label).strip()]

    choices = config.get("choices")
    if choices is None:
        # Neither `options` nor `choices` was resent: keep the existing options
        # (a config update shouldn't silently wipe the option set).
        return list((existing or {}).get("options", []) or [])

    return [raw.strip() for raw in choices.split(";") if raw.strip()]


def validate_config_constraints(column_type: ColumnType, config: dict) -> None:
    """Validate that a column's own settings are well-formed (min <= max, etc.)."""
    if column_type == ColumnType.text:
        mode = config.get("text_mode")
        if mode is not None and mode not in TEXT_MODES:
            raise ValidationError(
                f"'text_mode' must be one of {', '.join(TEXT_MODES)}"
            )
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

    elif column_type == ColumnType.rating:
        # `max` is the top of the scale, and it is the one config key on this type
        # a person actually sets. Rejected outright rather than clamped, because a
        # column silently drawn with five stars after somebody asked for twenty is
        # a setting that did not take and said nothing.
        top = config.get("max")
        if top is not None and (
            isinstance(top, bool)
            or not isinstance(top, int)
            or not 1 <= top <= RATING_MAX
        ):
            raise ValidationError(
                f"A rating column's 'max' must be a whole number from 1 to "
                f"{RATING_MAX}"
            )
        if config.get("min") is not None:
            # The bottom of the scale is 1, always: a rating of zero is what an
            # *empty* cell means, so a configurable floor would give the column two
            # ways to say "unrated" and no way to tell them apart.
            raise ValidationError(
                "A rating column has no 'min' — its scale starts at 1, and an "
                "unrated record is an empty cell"
            )
        icon = config.get("rating_icon")
        if icon is not None and icon not in _RATING_ICONS:
            raise ValidationError(
                f"'rating_icon' must be one of {', '.join(sorted(_RATING_ICONS))}"
            )

    elif column_type == ColumnType.duration:
        for key in ("min", "max"):
            v = config.get(key)
            if v is not None and (
                isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0
            ):
                raise ValidationError(
                    f"duration '{key}' must be a non-negative number of seconds"
                )
        lo, hi = config.get("min"), config.get("max")
        if lo is not None and hi is not None and lo > hi:
            raise ValidationError("'min' cannot exceed 'max'")
        fmt = config.get("duration_format")
        if fmt is not None and fmt not in _DURATION_FORMATS:
            raise ValidationError(
                f"'duration_format' must be one of {', '.join(sorted(_DURATION_FORMATS))}"
            )

    elif column_type == ColumnType.email:
        max_length = config.get("max_length")
        if max_length is not None and (
            isinstance(max_length, bool)
            or not isinstance(max_length, int)
            or max_length < 1
        ):
            raise ValidationError("'max_length' must be a positive integer")

    elif column_type == ColumnType.phone:
        import phonenumbers

        default_region, allowed = phone_regions(config)
        for region in ([default_region] if default_region else []) + allowed:
            if region not in phonenumbers.SUPPORTED_REGIONS:
                raise ValidationError(
                    f"'{region}' is not a country code this build knows; "
                    f"expected an ISO-3166 alpha-2 code like 'PH'"
                )
        # A default region outside the allowed set is a column nobody can fill by
        # typing a national number: the region that makes one parseable is the one
        # the check then rejects it for.
        if default_region and allowed and default_region not in allowed:
            raise ValidationError(
                f"'default_region' ({default_region}) must be one of "
                f"'allowed_regions' ({', '.join(allowed)})"
            )
        fmt = config.get("display_format")
        if fmt is not None and fmt not in _PHONE_FORMATS:
            raise ValidationError(
                f"'display_format' must be one of {', '.join(sorted(_PHONE_FORMATS))}"
            )

    elif column_type == ColumnType.location:
        centre = config.get("default_center")
        if centre is not None:
            if not isinstance(centre, dict) or set(centre) - {"lat", "lng"}:
                raise ValidationError(
                    "'default_center' must be a {lat, lng} object"
                )
            if centre.get("lat") is None or centre.get("lng") is None:
                raise ValidationError(
                    "'default_center' needs both a latitude and a longitude"
                )
            _coordinate(config, "default_center", centre)
        zoom = config.get("default_zoom")
        if zoom is not None and (
            isinstance(zoom, bool) or not isinstance(zoom, int) or not 0 <= zoom <= 20
        ):
            raise ValidationError("'default_zoom' must be a whole number from 0 to 20")
        link = config.get("map_link")
        if link is not None and link not in _MAP_LINKS:
            raise ValidationError(
                f"'map_link' must be one of {', '.join(sorted(_MAP_LINKS))}"
            )

    elif column_type == ColumnType.color:
        swatches = config.get("swatches")
        if swatches is not None:
            if not isinstance(swatches, list):
                raise ValidationError("'swatches' must be a list of colours")
            for swatch in swatches:
                if not isinstance(swatch, str) or not _COLOR_RE.fullmatch(
                    normalize_color(swatch)
                ):
                    raise ValidationError(
                        f"'{swatch}' is not a colour; expected #rrggbb"
                    )

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
    if column_type == ColumnType.text:
        if not isinstance(value, str):
            _expect(name, "a string")
        # A rich cell holds HTML, so it is measured and matched on what the
        # writer can actually see. Counting the markup would make a 200-character
        # limit depend on how much formatting they used, and a `pattern` written
        # for prose would be tested against tags. Imported here: richtext pulls
        # SAFE_LINK_SCHEMES from this module.
        from .richtext import is_rich, to_text

        rich = is_rich(config)
        measured = to_text(value) if rich else value
        lo, hi = config.get("min_length"), config.get("max_length")
        if lo is not None and len(measured) < lo:
            raise ValidationError(f"Column '{name}' must be at least {lo} character(s)")
        if hi is not None and len(measured) > hi:
            raise ValidationError(f"Column '{name}' must be at most {hi} character(s)")
        pattern = config.get("pattern")
        if pattern is not None and re.fullmatch(pattern, measured) is None:
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

    elif column_type == ColumnType.rating:
        top = rating_scale(config)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _expect(name, f"a rating from 1 to {top}")
        if isinstance(value, float) and not value.is_integer():
            _expect(name, f"a whole rating from 1 to {top}")
        if not 1 <= value <= top:
            raise ValidationError(
                f"Column '{name}' must be a rating from 1 to {top}; leave it empty "
                f"for unrated"
            )

    elif column_type == ColumnType.duration:
        # Seconds. Not required to be whole — half a second is a length of time —
        # but never negative, which no reading of a duration produces.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _expect(name, "a duration in seconds")
        if value < 0:
            raise ValidationError(f"Column '{name}' cannot be a negative duration")
        lo, hi = config.get("min"), config.get("max")
        if lo is not None and value < lo:
            raise ValidationError(f"Column '{name}' must be at least {lo} second(s)")
        if hi is not None and value > hi:
            raise ValidationError(f"Column '{name}' must be at most {hi} second(s)")

    elif column_type == ColumnType.checkbox:
        if not isinstance(value, bool):
            _expect(name, "a boolean")

    elif column_type == ColumnType.choice:
        valid = set(config.get("options", []) or [])
        # With `allow_other`, the options are what the column *offers*, not what it
        # permits: anything typed into the "Other" box is a valid answer. It still
        # has to be a string with something in it — a blank one is an empty cell
        # spelled awkwardly, and the caller means null.
        other = bool(config.get("allow_other"))
        if config.get("multiple"):
            if not isinstance(value, list):
                _expect(name, "a list of options")
            candidates = value
        else:
            candidates = [value]
        for record in candidates:
            # `isinstance` first, and not only for speed: an unhashable value —
            # a dict or table arriving in a cell — raises out of a set membership
            # test rather than failing validation, which is a 500 for what is
            # plainly a bad request.
            if isinstance(record, str) and record in valid:
                continue
            if not other:
                raise ValidationError(
                    f"'{record}' is not a valid option for column '{name}'"
                )
            if not isinstance(record, str) or not record.strip():
                raise ValidationError(
                    f"Column '{name}' expects one of its options or an answer of "
                    f"your own, not {record!r}"
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

    elif column_type == ColumnType.email:
        if not isinstance(value, str):
            _expect(name, "an email address")
        # Checked against the stored form, because that is what everything
        # downstream sees: `_validate_value` normalizes before this runs on the
        # record path, and `_validate_config` does the same to a default.
        address = normalize_email(value)
        if not _EMAIL_RE.fullmatch(address):
            raise ValidationError(
                f"Column '{name}' expects an email address (e.g. name@example.com)"
            )
        hi = config.get("max_length")
        if hi is not None and len(address) > hi:
            raise ValidationError(f"Column '{name}' must be at most {hi} character(s)")

    elif column_type == ColumnType.phone:
        if not isinstance(value, str):
            _expect(name, "a phone number")
        # `normalize_phone` *is* the check — it raises for everything this would
        # otherwise have to re-test, and the write path has already run it, so this
        # is what makes a value arriving any other way (a column default, a
        # conversion) meet the same bar.
        normalize_phone(config, name, value)

    elif column_type == ColumnType.location:
        # The one cell shape in the system that is an object rather than a scalar or
        # a list of them, which is why the key set is closed: `data` is schemaless,
        # so nothing but this check stops a location cell becoming a place to stash
        # arbitrary JSON that every reader downstream would then have to survive.
        if not isinstance(value, dict):
            _expect(name, "a location ({address, lat, lng})")
        unknown = sorted(set(value) - set(LOCATION_KEYS))
        if unknown:
            raise ValidationError(
                f"Column '{name}' has no place for {', '.join(unknown)}; a location "
                f"holds {', '.join(LOCATION_KEYS)}"
            )
        address = value.get("address")
        if address is not None and not isinstance(address, str):
            raise ValidationError(f"Column '{name}': address must be text")
        if isinstance(address, str) and len(address) > _ADDRESS_MAX:
            raise ValidationError(
                f"Column '{name}': address must be at most {_ADDRESS_MAX} characters"
            )
        _coordinate(config, name, value)
        has_address = isinstance(address, str) and address.strip() != ""
        if not has_address and value.get("lat") is None:
            # Neither half filled in is an empty cell spelled awkwardly. Refused
            # rather than silently nulled, so `required` cannot be satisfied by one.
            raise ValidationError(
                f"Column '{name}' needs an address or a pair of coordinates"
            )
        if config.get("require_coordinates") and value.get("lat") is None:
            raise ValidationError(
                f"Column '{name}' needs coordinates, not only an address"
            )

    elif column_type == ColumnType.color:
        if not isinstance(value, str):
            _expect(name, "a colour (#rrggbb)")
        if not _COLOR_RE.fullmatch(normalize_color(value)):
            raise ValidationError(
                f"Column '{name}' expects a colour like #1d4ed8, not {value!r}"
            )

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
