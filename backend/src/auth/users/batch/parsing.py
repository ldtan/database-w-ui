"""Turn the uploaded CSV into validated rows, without touching the database.

Pure and synchronous on purpose. Everything knowable from the file alone is settled
here, so the import service only ever sees rows that are already well-formed -- and
so these rules can be tested without Postgres, which is where most of the behaviour
lives.
"""
import csv
import io
from typing import NamedTuple, Optional

from pydantic import ValidationError as PydanticValidationError

from src.core.exceptions import ValidationError
from src.people.schemas import PersonCreate

from .schemas import BatchUserError, BatchUserRow

ACCOUNT_HEADERS = (
    "username", "email", "password", "is_active", "is_superuser", "groups",
)
PERSON_HEADERS = (
    "name_prefix", "first_name", "middle_name", "last_name", "name_suffix",
    "birth_date",
)
KNOWN_HEADERS = (*ACCOUNT_HEADERS, *PERSON_HEADERS)
REQUIRED_HEADERS = ("username", "email")

# Each row is several round trips, so an unbounded file is a request that never
# returns. Parsing is pure, so hitting this cap still means nothing was written.
MAX_ROWS = 1000


class ParsedFile(NamedTuple):
    rows: list[tuple[int, BatchUserRow]]
    errors: list[BatchUserError]


def _flatten(exc: PydanticValidationError) -> str:
    """Pydantic's error list as one line, keeping the field name that failed."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or 'row'}: {err['msg']}"
        for err in exc.errors()
    )


def check_headers(fieldnames: Optional[list[str]]) -> None:
    """Reject a file whose columns are wrong, before a single account exists.

    Unknown headers are an error here where list import ignores them, because this
    column set is *closed*: `frist_name` cannot be another list's column, so it is a
    file that would silently drop every name in it. Missing required headers get
    their own message for the same reason read the other way -- without it every row
    fails identically, and a thousand copies of one error describe a file-level
    problem far worse than one sentence does.
    """
    present = [name.strip() for name in (fieldnames or []) if name and name.strip()]
    if not present:
        raise ValidationError("The file has no header row")

    unknown = [name for name in present if name not in KNOWN_HEADERS]
    if unknown:
        raise ValidationError(
            f"Unknown column(s): {', '.join(sorted(unknown))}. "
            f"Expected any of: {', '.join(KNOWN_HEADERS)}"
        )

    missing = [name for name in REQUIRED_HEADERS if name not in present]
    if missing:
        raise ValidationError(f"Missing required column(s): {', '.join(missing)}")


def _person_from(values: dict) -> Optional[PersonCreate]:
    """The row's profile, or None when it has none.

    Every person cell blank means an account with no profile -- the same outcome as
    leaving "Connect a person record" unchecked in the Add user dialog. Some cells
    filled but a name missing is an error rather than a profile quietly dropped:
    dropping it is the one outcome nothing would report.
    """
    given = {key: value for key, value in values.items() if key in PERSON_HEADERS}
    if not given:
        return None
    missing = [key for key in ("first_name", "last_name") if not given.get(key)]
    if missing:
        raise ValidationError(
            "A profile needs both first_name and last_name; "
            f"missing: {', '.join(missing)}"
        )
    return PersonCreate(**given)


def parse_file(text: str) -> ParsedFile:
    """Validate the whole upload up front.

    Whole rather than streaming, deliberately -- it is what lets the header check and
    the row cap fail before any account exists, and it lets the import service
    resolve every group name in the file in one query instead of one per row.
    """
    reader = csv.DictReader(io.StringIO(text))
    check_headers(reader.fieldnames)

    rows: list[tuple[int, BatchUserRow]] = []
    errors: list[BatchUserError] = []

    for raw in reader:
        # reader.line_num is the row's real source line, so a reported number still
        # points at the right line when the file has blank ones. Same reasoning as
        # lists/transfer/routers.py.
        number = reader.line_num
        if len(rows) + len(errors) >= MAX_ROWS:
            raise ValidationError(
                f"This file has more than {MAX_ROWS} rows; split it and import "
                "each part separately"
            )

        # A blank cell means "not given", so it is dropped rather than passed as "":
        # that is what lets `is_active` fall back to its default and `password` mean
        # "mint one". The None check guards a short row, which DictReader pads.
        values = {
            key: value.strip()
            for key, value in raw.items()
            if key in KNOWN_HEADERS and value is not None and value.strip()
        }

        try:
            person = _person_from(values)
            row = BatchUserRow(
                person=person,
                **{k: v for k, v in values.items() if k in ACCOUNT_HEADERS},
            )
        except ValidationError as exc:
            errors.append(BatchUserError(row=number, message=str(exc)))
        except PydanticValidationError as exc:
            errors.append(BatchUserError(row=number, message=_flatten(exc)))
        else:
            rows.append((number, row))

    return ParsedFile(rows=rows, errors=errors)
