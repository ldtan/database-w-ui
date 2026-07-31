# Batch user creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A superuser uploads one CSV and gets many accounts — each with an optional person record, group membership and activation state — instead of working the Add user dialog once per person.

**Architecture:** A new `backend/src/auth/users/batch/` package laid out like `lists/transfer/`, exposing `POST /api/auth/users/import`. The file is parsed whole and validated in memory first (pure, no database), then each row is created through the *existing* `UserService` / `PersonService`, so a batch-created account is indistinguishable from a form-created one. Those services commit internally, so every row is already its own transaction — which is exactly the isolation skip-and-report wants.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2 async, PostgreSQL, pytest/pytest-asyncio, httpx ASGITransport · React 18, TanStack Query, PrimeReact, vitest (node environment, no jsdom)

**Global Constraints:**
- Design doc is `docs/superpowers/specs/2026-07-31-user-batch-creation-design.md` (commit `273bce8`). It is binding; deviations need a note in the commit message.
- Twelve CSV headers exactly: `username`, `email`, `password`, `is_active`, `is_superuser`, `groups`, `name_prefix`, `first_name`, `middle_name`, `last_name`, `name_suffix`, `birth_date`.
- `;` is the multi-value separator and `true/false/1/0/yes/no` the boolean spellings — both taken from `lists/transfer/services.py::_parse_cell` so users meet one file convention, not two.
- Unknown headers → 400. Missing `username`/`email` header → 400. More than 1000 data rows → 400. Headers with no data rows → 200 and an empty summary.
- Generated passwords appear in the response body only. Never logged, never persisted, never re-retrievable.
- Row pipeline order is fixed: validate in memory → generate password → create account → create profile → attach groups → promote. Promotion is last so the row-undo never trips `_guard_last_superuser`.
- Superuser-only, via the existing `Depends(require_superuser)`.
- Commit messages: repo style — imperative subject plus reasoning prose. **No AI attribution, no `Co-Authored-By`, no "generated with" line.**

**User decisions (already made):**
- "Optional column, generate if blank" — the `password` column may be filled per row; blank means the server mints one.
- "groups, is_active, is_superuser" — all three optional columns are in scope.
- "Skip and report, like list import" — per-row errors, good rows commit; no dry-run, no all-or-nothing.
- "One-time credentials CSV download" — generated passwords leave via a browser-built CSV, not an on-screen table.
- "Go with A" — dedicated batch module reusing the existing services, rather than generalizing the list-import engine or looping the API from the browser.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/src/auth/users/batch/__init__.py` | package marker |
| `backend/src/auth/users/batch/schemas.py` | `BatchUserRow`, `CreatedUser`, `BatchUserError`, `BatchUserSummary` |
| `backend/src/auth/users/batch/parsing.py` | headers, boolean/group coercion, the profile rule, `parse_file` — **pure, no database** |
| `backend/src/auth/users/batch/services.py` | `resolve_groups`, `import_users` — the row pipeline |
| `backend/src/auth/users/batch/routers.py` | `POST /import` |
| `backend/src/auth/users/routers.py` | modified: include the batch router |
| `backend/tests/test_user_batch_parsing.py` | the pure rules, no Postgres needed |
| `backend/tests/test_user_batch_import.py` | pipeline + endpoint, against the test database |
| `frontend/src/features/people/credentials.ts` | `credentialsCsv` — pure |
| `frontend/src/features/people/credentials.test.ts` | vitest for the above |
| `frontend/src/features/people/BatchUserDialog.tsx` | the dialog |
| `frontend/src/features/people/PeoplePage.tsx` | modified: "Import users" button + dialog |

**Note on `parsing.py`:** the design doc names three files (`schemas`/`services`/`routers`, mirroring `lists/transfer/`). Splitting the pure parsing rules into a fourth is a deliberate refinement: it keeps `services.py` about the database and makes every file-format rule testable without Postgres, which is where most of the behaviour lives. Mention it in the Task 1 commit body.

---

### Task 1: Row schemas and the CSV parser

**Goal:** Everything knowable from the file alone — headers, types, the profile rule — validated in memory, with no database involved.

**Files:**
- Create: `backend/src/auth/users/batch/__init__.py`
- Create: `backend/src/auth/users/batch/schemas.py`
- Create: `backend/src/auth/users/batch/parsing.py`
- Test: `backend/tests/test_user_batch_parsing.py`

**Acceptance Criteria:**
- [ ] An unknown header raises `ValidationError` naming the offending column and listing the expected set
- [ ] A missing `username` or `email` header raises `ValidationError` naming what is missing
- [ ] A file with more than 1000 data rows raises `ValidationError`
- [ ] Headers with no data rows parses to empty `rows` and empty `errors`
- [ ] `is_active`/`is_superuser` accept `true/false/1/0/yes/no` case-insensitively; anything else is a row error
- [ ] `groups` splits on `;` and drops blank segments
- [ ] A row with no person cells yields `person=None`; a row with any person cell but no `last_name` is a row error
- [ ] A blank cell means "not given" — `is_active` falls back to `True`, `password` to `None`
- [ ] Row numbers are the file's real line numbers, so blank lines do not shift them

**Verify:** `cd backend && uv run --system-certs pytest tests/test_user_batch_parsing.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_user_batch_parsing.py`:

```python
"""The file-format rules, with no database in sight.

Everything here is pure: `parse_file` settles what can be known from the upload
alone, so the import service only ever sees well-formed rows. Keeping these tests
free of Postgres is the point -- they are the bulk of the behaviour and they run in
milliseconds.
"""
import pytest

from src.auth.users.batch.parsing import MAX_ROWS, parse_file
from src.core.exceptions import ValidationError

HEADER = "username,email,password,is_active,is_superuser,groups"
PERSON_HEADER = "name_prefix,first_name,middle_name,last_name,name_suffix,birth_date"


def test_minimal_file_parses():
    parsed = parse_file(f"username,email\nada,ada@example.com\n")
    assert parsed.errors == []
    (number, row), = parsed.rows
    assert number == 2  # line 1 is the header
    assert (row.username, row.email) == ("ada", "ada@example.com")
    # A blank cell means "not given", so the defaults apply.
    assert row.password is None
    assert row.is_active is True
    assert row.is_superuser is False
    assert row.groups == []
    assert row.person is None


def test_unknown_header_is_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_file("username,email,frist_name\nada,ada@example.com,Ada\n")
    assert "frist_name" in str(exc.value)


def test_missing_required_header_is_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_file("email\nada@example.com\n")
    assert "username" in str(exc.value)


def test_no_header_row_is_rejected():
    with pytest.raises(ValidationError):
        parse_file("")


def test_headers_with_no_rows_is_not_an_error():
    parsed = parse_file("username,email\n")
    assert parsed.rows == []
    assert parsed.errors == []


def test_row_cap_is_enforced():
    body = "".join(f"u{i},u{i}@example.com\n" for i in range(MAX_ROWS + 1))
    with pytest.raises(ValidationError) as exc:
        parse_file(f"username,email\n{body}")
    assert str(MAX_ROWS) in str(exc.value)


@pytest.mark.parametrize(
    "cell,expected",
    [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("y", True),
     ("false", False), ("0", False), ("no", False), ("N", False)],
)
def test_boolean_spellings(cell, expected):
    parsed = parse_file(f"username,email,is_active\nada,ada@example.com,{cell}\n")
    assert parsed.rows[0][1].is_active is expected


def test_unparseable_boolean_is_a_row_error():
    parsed = parse_file("username,email,is_active\nada,ada@example.com,maybe\n")
    assert parsed.rows == []
    assert "maybe" in parsed.errors[0].message
    assert parsed.errors[0].row == 2


def test_groups_split_on_semicolons():
    parsed = parse_file(
        f"username,email,groups\nada,ada@example.com,Teachers; Year 9 ;;\n"
    )
    assert parsed.rows[0][1].groups == ["Teachers", "Year 9"]


def test_a_row_with_no_person_cells_has_no_profile():
    parsed = parse_file(f"username,email,{PERSON_HEADER}\nada,ada@example.com,,,,,,\n")
    assert parsed.rows[0][1].person is None


def test_a_full_profile_is_validated_by_person_create():
    parsed = parse_file(
        f"username,email,{PERSON_HEADER}\n"
        "ada,ada@example.com,Dr,Ada,King,Lovelace,Jr,1815-12-10\n"
    )
    person = parsed.rows[0][1].person
    assert (person.name_prefix, person.first_name, person.last_name) == ("Dr", "Ada", "Lovelace")
    assert person.birth_date.isoformat() == "1815-12-10"


def test_a_partial_profile_is_a_row_error():
    parsed = parse_file(
        f"username,email,{PERSON_HEADER}\nada,ada@example.com,,Ada,,,,\n"
    )
    assert parsed.rows == []
    assert "last_name" in parsed.errors[0].message


def test_a_future_birth_date_is_a_row_error():
    parsed = parse_file(
        f"username,email,{PERSON_HEADER}\n"
        "ada,ada@example.com,,Ada,,Lovelace,,3000-01-01\n"
    )
    assert parsed.rows == []
    assert "birth_date" in parsed.errors[0].message


def test_a_bad_email_is_a_row_error_not_a_file_error():
    parsed = parse_file("username,email\ngood,good@example.com\nbad,nope\n")
    assert len(parsed.rows) == 1
    assert parsed.errors[0].row == 3


def test_row_numbers_are_real_line_numbers():
    """Blank lines must not shift the number a person reads in the error list."""
    parsed = parse_file("username,email\n\nada,ada@example.com\n")
    assert parsed.rows[0][0] == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run --system-certs pytest tests/test_user_batch_parsing.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'src.auth.users.batch'`

- [ ] **Step 3: Write the schemas**

Create `backend/src/auth/users/batch/__init__.py` (empty file).

Create `backend/src/auth/users/batch/schemas.py`:

```python
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.people.schemas import PersonCreate

# The spellings list import already accepts, so someone who has used that file format
# does not have to learn a second one. See lists/transfer/services.py::_parse_cell.
_TRUE = {"true", "1", "yes", "y"}
_FALSE = {"false", "0", "no", "n"}


class BatchUserRow(BaseModel):
    """One validated line of the upload.

    Carries a fully-validated ``PersonCreate`` rather than loose name fields, so the
    profile rules -- lengths, both names required, birth date not in the future --
    are the *same* ones the /people endpoint applies. There is no second copy to
    drift out of step.

    ``extra="forbid"`` is belt-and-braces: the parser only ever passes keys it knows,
    so this fires when a header is added to ``KNOWN_HEADERS`` without a field to
    receive it -- which would otherwise be a column that silently does nothing.
    """

    username: str = Field(min_length=1, max_length=50)
    email: EmailStr
    # None means "the server should mint one". The summary reports which rows got one.
    password: Optional[str] = None
    is_active: bool = True
    is_superuser: bool = False
    groups: list[str] = Field(default_factory=list)
    person: Optional[PersonCreate] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("is_active", "is_superuser", mode="before")
    @classmethod
    def _coerce_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"'{value}' is not true/false; use true/false, 1/0 or yes/no")

    @field_validator("groups", mode="before")
    @classmethod
    def _split_groups(cls, value: object) -> object:
        if isinstance(value, list):
            return value
        return [name.strip() for name in str(value).split(";") if name.strip()]


class BatchUserError(BaseModel):
    row: int
    message: str


class CreatedUser(BaseModel):
    row: int
    uuid: UUID
    username: str
    # Set only when the server minted the password. Returned exactly once and never
    # stored -- there is no way to ask for it again, by design.
    generated_password: Optional[str] = None


class BatchUserSummary(BaseModel):
    created: list[CreatedUser] = Field(default_factory=list)
    errors: list[BatchUserError] = Field(default_factory=list)
    # Reported on its own so a typo'd `is_superuser` column is visible in the result
    # rather than buried among the successes.
    superusers_created: int = 0
```

- [ ] **Step 4: Write the parser**

Create `backend/src/auth/users/batch/parsing.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run --system-certs pytest tests/test_user_batch_parsing.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/src/auth/users/batch backend/tests/test_user_batch_parsing.py
git commit -F - <<'EOF'
Validate a batch-user upload before the database sees it

Everything knowable from the file alone -- the header set, the boolean and
group spellings, whether a row has a profile at all -- is settled here, so
the import service that follows only ever deals with rows that are already
well-formed. Pure and synchronous, which also means these rules test in
milliseconds instead of against Postgres.

Unknown headers are rejected rather than ignored, inverting what list import
does. There, a header this list lacks may well be another list's column; here
the set is closed, so `frist_name` is a file that would silently drop every
name in it. A missing `username` header gets its own message for the same
reason read backwards -- otherwise every row fails identically and a thousand
copies of one error hide a problem one sentence describes.

The row carries a validated PersonCreate rather than loose name fields, so the
profile rules are literally the ones /people applies rather than a second copy
free to drift. Splitting this off from services.py departs from the three-file
layout the design sketched; the file-format rules and the database work turned
out to want different tests.
EOF
```

---

### Task 2: The row pipeline

**Goal:** Turn parsed rows into accounts, profiles, memberships and promotions — reusing the services a single Add user already goes through.

**Files:**
- Create: `backend/src/auth/users/batch/services.py`
- Test: `backend/tests/test_user_batch_import.py`

**Acceptance Criteria:**
- [ ] A created account has its personal workspace, exactly as `UserService.create` gives a form-created one
- [ ] A blank `password` cell yields a `generated_password` in the summary; a filled one yields `None`
- [ ] Both the generated and the supplied password actually authenticate
- [ ] A duplicate username inside one file errors that row and leaves the first intact
- [ ] An unknown group name errors the row and creates no account; a known one attaches
- [ ] `is_superuser` promotion takes effect and is counted in `superusers_created`
- [ ] A row that fails after its account exists leaves no live account behind
- [ ] Parse errors and pipeline errors come back in one list, ordered by row

**Verify:** `cd backend && uv run --system-certs pytest tests/test_user_batch_import.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_user_batch_import.py`:

```python
"""Creating many accounts from one file.

Runs against the test database because the point of the pipeline is that it goes
through the same services a single Add user does -- the file-format rules are tested
without Postgres in test_user_batch_parsing.py.
"""
import pytest
import pytest_asyncio

from src.auth.helpers import verify_password
from src.auth.users.batch.parsing import parse_file
from src.auth.users.batch.services import import_users
from src.auth.users.schemas import UserCreate, UserGroupCreate
from src.auth.users.services import UserGroupService, UserService
from src.people.services import PersonService
from src.workspaces.services import WorkspaceService

HEADER = "username,email,password,is_active,is_superuser,groups"
PERSON_HEADER = "name_prefix,first_name,middle_name,last_name,name_suffix,birth_date"


async def _run(db, text):
    return await import_users(db, parse_file(text))


@pytest_asyncio.fixture
async def db(session_factory, user_id):
    # user_id seeds the first account, so the ones created here are never the only
    # superuser -- the guard in UserService is not what is under test.
    async with session_factory() as session:
        yield session


async def test_creates_accounts_with_workspaces(db):
    summary = await _run(
        db,
        "username,email\nada,ada@example.com\ngrace,grace@example.com\n",
    )
    assert summary.errors == []
    assert [c.username for c in summary.created] == ["ada", "grace"]
    assert [c.row for c in summary.created] == [2, 3]

    ada = await UserService.get_by_username(db, "ada")
    assert ada is not None
    # A batch-created account must be indistinguishable from a form-created one, and
    # the personal workspace is the part most easily lost by inlining creation.
    assert await WorkspaceService.list(db, ada.id, limit=1)


async def test_generated_password_is_returned_once_and_works(db):
    summary = await _run(db, "username,email\nada,ada@example.com\n")
    created, = summary.created
    assert created.generated_password
    ada = await UserService.get_by_username(db, "ada")
    assert verify_password(created.generated_password, ada.password)


async def test_supplied_password_is_used_and_not_echoed(db):
    summary = await _run(
        db, f"{HEADER}\nada,ada@example.com,hunter22,,,\n"
    )
    created, = summary.created
    # Nothing to hand back: the admin already knows it.
    assert created.generated_password is None
    ada = await UserService.get_by_username(db, "ada")
    assert verify_password("hunter22", ada.password)


async def test_is_active_false_is_honoured(db):
    await _run(db, f"{HEADER}\nada,ada@example.com,,false,,\n")
    ada = await UserService.get_by_username(db, "ada")
    assert ada.is_active is False


async def test_profile_is_created_when_the_row_has_one(db):
    await _run(
        db,
        f"username,email,{PERSON_HEADER}\n"
        "ada,ada@example.com,Dr,Ada,King,Lovelace,Jr,1815-12-10\n",
    )
    ada = await UserService.get_by_username(db, "ada")
    person = await PersonService.get_by_user_id(db, ada.id)
    assert (person.name_prefix, person.first_name, person.last_name) == (
        "Dr", "Ada", "Lovelace",
    )
    assert person.birth_date.isoformat() == "1815-12-10"


async def test_no_profile_when_every_person_cell_is_blank(db):
    await _run(db, "username,email\nada,ada@example.com\n")
    ada = await UserService.get_by_username(db, "ada")
    assert await PersonService.get_by_user_id(db, ada.id) is None


async def test_duplicate_username_in_one_file_errors_only_the_second_row(db):
    summary = await _run(
        db,
        "username,email\nada,ada@example.com\nada,other@example.com\n",
    )
    assert [c.row for c in summary.created] == [2]
    assert [e.row for e in summary.errors] == [3]
    assert "already in use" in summary.errors[0].message


async def test_unknown_group_errors_the_row_and_creates_nothing(db):
    summary = await _run(
        db, f"{HEADER}\nada,ada@example.com,,,,Nonexistent\n"
    )
    assert summary.created == []
    assert "Nonexistent" in summary.errors[0].message
    assert await UserService.get_by_username(db, "ada") is None


async def test_known_groups_are_attached(db):
    await UserGroupService.create(db, UserGroupCreate(name="Teachers"))
    await UserGroupService.create(db, UserGroupCreate(name="Year 9"))

    summary = await _run(
        db, f"{HEADER}\nada,ada@example.com,,,,Teachers;Year 9\n"
    )
    assert summary.errors == []
    ada = await UserService.get_by_uuid(db, summary.created[0].uuid)
    assert sorted(group.name for group in ada.groups) == ["Teachers", "Year 9"]


async def test_superuser_promotion_is_applied_and_counted(db):
    summary = await _run(
        db, f"{HEADER}\nroot,root@example.com,,,true,\n"
    )
    assert summary.superusers_created == 1
    root = await UserService.get_by_username(db, "root")
    assert root.is_superuser is True


async def test_parse_errors_and_pipeline_errors_arrive_together_in_row_order(db):
    summary = await _run(
        db,
        "username,email\n"
        "ada,ada@example.com\n"   # ok
        "bad,not-an-email\n"      # parse error
        "ada,dupe@example.com\n"  # pipeline error
    )
    assert [c.row for c in summary.created] == [2]
    assert [e.row for e in summary.errors] == [3, 4]


async def test_a_row_failing_after_its_account_exists_leaves_nothing_live(db, monkeypatch):
    """The summary promises a reported row created nothing. Step 1 of the pipeline
    makes this nearly unreachable, so it is forced here."""
    async def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(PersonService, "create", boom)
    summary = await _run(
        db,
        f"username,email,{PERSON_HEADER}\n"
        "ada,ada@example.com,,Ada,,Lovelace,,\n",
    )
    assert summary.created == []
    assert "disk on fire" in summary.errors[0].message
    assert await UserService.get_by_username(db, "ada") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run --system-certs pytest tests/test_user_batch_import.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'src.auth.users.batch.services'`

- [ ] **Step 3: Write the service**

Create `backend/src/auth/users/batch/services.py`:

```python
"""Create many accounts from one uploaded file.

Every row goes through the same services a single Add user does -- UserService,
PersonService -- so a batch-created account is indistinguishable from a form-created
one: same argon2 hashing, same conflict message, same personal workspace. Inlining
account creation here would be faster and is the wrong trade: it is how batch quietly
stops bootstrapping a workspace months after anyone remembers there are two paths.

``UserService.create`` and ``WorkspaceService.create`` each commit internally, so
every row is already its own transaction. That is exactly the isolation
skip-and-report wants, which is why there is no savepoint machinery here -- unlike
list import, which inserts into one session and commits in batches for speed.
"""
import secrets
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ServiceError, UnknownReferenceError
from src.people.services import PersonService

from ..models import UserGroup
from ..schemas import UserCreate, UserUpdate
from ..services import UserService
from .parsing import ParsedFile
from .schemas import BatchUserError, BatchUserSummary, CreatedUser

# 16 URL-safe characters, 96 bits of entropy. Long enough that it never needs a
# strength policy, short enough to read off a page and type.
_PASSWORD_BYTES = 12


async def resolve_groups(db: AsyncSession, names: set[str]) -> dict[str, UserGroup]:
    """Every group named anywhere in the file, in one query rather than one per row.

    This is what parsing the whole upload up front buys: the names are all known
    before the first account is created.
    """
    if not names:
        return {}
    result = await db.execute(
        select(UserGroup).where(
            UserGroup.name.in_(names), UserGroup.deleted_at.is_(None)
        )
    )
    return {group.name: group for group in result.scalars().all()}


async def _undo_row(db: AsyncSession, user_uuid: UUID) -> None:
    """Take back an account whose row failed after it had been created.

    Keeps the promise the summary makes -- a reported row created nothing. Validating
    the whole row in memory first makes this nearly unreachable; what is left is
    infrastructure failure and a group deleted mid-import. Rolling back first is what
    makes the delete possible at all, since the session may still be holding the
    failure that got us here.
    """
    try:
        await db.rollback()
        await UserService.delete(db, user_uuid)
    except Exception:
        # Nothing better is available, and the caller still has to hear about the
        # error that got us here -- which is the more useful of the two.
        pass


async def import_users(db: AsyncSession, parsed: ParsedFile) -> BatchUserSummary:
    """Create an account per parsed row, reporting the ones that could not be."""
    groups_by_name = await resolve_groups(
        db, {name for _, row in parsed.rows for name in row.groups}
    )

    created: list[CreatedUser] = []
    errors: list[BatchUserError] = list(parsed.errors)
    superusers_created = 0

    for number, row in parsed.rows:
        password = row.password or secrets.token_urlsafe(_PASSWORD_BYTES)
        try:
            unknown = [name for name in row.groups if name not in groups_by_name]
            if unknown:
                raise UnknownReferenceError(f"Unknown group(s): {', '.join(unknown)}")
            user = await UserService.create(
                db,
                UserCreate(
                    username=row.username,
                    email=row.email,
                    password=password,
                    is_active=row.is_active,
                ),
            )
        except ServiceError as exc:
            errors.append(BatchUserError(row=number, message=str(exc)))
            continue

        # Read the identifiers now and use them from here on: the row still has
        # commits ahead of it, and what gets reported has to survive them.
        user_uuid, user_id, username = user.uuid, user.id, user.username
        try:
            if row.person is not None:
                await PersonService.create(db, user_id, row.person)
            if row.groups:
                # Assigned on the *user*, not through UserGroupService.update, which
                # sets a group's entire membership from a uuid list -- calling that
                # per row would erase everyone already in the group.
                user.groups = [groups_by_name[name] for name in row.groups]
                await db.commit()
            if row.is_superuser:
                # UserCreate has no is_superuser field on purpose ("an account cannot
                # be *born* a superuser"), so this mirrors the follow-up PATCH the Add
                # user dialog already does. Last in the sequence, which is what keeps
                # _undo_row clear of _guard_last_superuser.
                await UserService.update(db, user_uuid, UserUpdate(is_superuser=True))
                superusers_created += 1
        except Exception as exc:
            await _undo_row(db, user_uuid)
            errors.append(BatchUserError(row=number, message=str(exc) or repr(exc)))
            continue

        created.append(
            CreatedUser(
                row=number,
                uuid=user_uuid,
                username=username,
                generated_password=None if row.password else password,
            )
        )

    # Parse errors were collected before any of these, so sorting is what puts the
    # two kinds back into the order the file reads in.
    errors.sort(key=lambda error: error.row)
    return BatchUserSummary(
        created=created, errors=errors, superusers_created=superusers_created
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run --system-certs pytest tests/test_user_batch_import.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/src/auth/users/batch/services.py backend/tests/test_user_batch_import.py
git commit -F - <<'EOF'
Create the accounts a parsed batch file describes

Each row goes through UserService and PersonService rather than inlining the
inserts, so a batch-created account is indistinguishable from a form-created
one -- same hashing, same conflict message, same personal workspace bootstrap.
Inlining would save a few round trips and cost the thing that matters: the
personal workspace is exactly the sort of step a second creation path stops
doing quietly, long after anyone remembers there are two.

Those services commit internally, which means every row is already its own
transaction, so skip-and-report needs no savepoint machinery here -- list
import has it because it batches inserts into one session for speed, and this
does not.

The ordering is load-bearing. Group names resolve in one query before the
first account exists, because the whole file was parsed up front. Promotion
runs last so that undoing a half-finished row never has to argue with the
last-superuser guard. And a row that fails after its account exists takes the
account back, so the summary's implicit promise -- a reported row created
nothing -- actually holds.
EOF
```

---

### Task 3: The endpoint

**Goal:** `POST /api/auth/users/import` accepts a multipart upload, superuser-only, mapping file-level problems to 400 and returning the summary.

**Files:**
- Create: `backend/src/auth/users/batch/routers.py`
- Modify: `backend/src/auth/users/routers.py` (imports at top, include after the router definitions)
- Test: `backend/tests/test_user_batch_import.py` (append)

**Acceptance Criteria:**
- [ ] A superuser posting a valid file gets 200 and the summary
- [ ] A non-superuser gets 403 and nothing is created
- [ ] An unknown header, a missing required header and an over-cap file each give 400
- [ ] A file that is not UTF-8 gives 400 rather than a 500
- [ ] The response matches `BatchUserSummary` and appears in the OpenAPI document

**Verify:** `cd backend && uv run --system-certs pytest tests/test_user_batch_import.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_user_batch_import.py`:

```python
# ---------------------------------------------------------------- endpoint

from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.infrastructure import database as db_module


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """A client acting as a specific user.

    conftest's `client` runs with DISABLE_AUTH, which resolves to the first account
    in the table -- not a superuser, so it cannot reach this endpoint at all. Same
    approach as test_people.py.
    """

    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine = engine
        db_module.SessionLocal = session_factory
        app = create_app()

        async def _current_user():
            return user

        app.dependency_overrides[get_current_user] = _current_user
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def _admin(session_factory):
    async with session_factory() as db:
        user = await UserService.create(
            db,
            UserCreate(username="root2", email="root2@example.com", password="pw12345"),
        )
        user.is_superuser = True
        await db.commit()
        return user


def _upload(text: str):
    return {"file": ("users.csv", text, "text/csv")}


async def test_endpoint_creates_users(session_factory, make_client):
    admin = await _admin(session_factory)
    async with make_client(admin) as client:
        resp = await client.post(
            "/api/auth/users/import",
            files=_upload("username,email\nada,ada@example.com\n"),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"][0]["username"] == "ada"
    assert body["created"][0]["generated_password"]
    assert body["errors"] == []
    assert body["superusers_created"] == 0


async def test_endpoint_is_superuser_only(session_factory, make_client):
    async with session_factory() as db:
        plain = await UserService.create(
            db,
            UserCreate(username="plain", email="plain@example.com", password="pw12345"),
        )
    async with make_client(plain) as client:
        resp = await client.post(
            "/api/auth/users/import",
            files=_upload("username,email\nsneaky,sneaky@example.com\n"),
        )
    assert resp.status_code == 403
    async with session_factory() as db:
        assert await UserService.get_by_username(db, "sneaky") is None


@pytest.mark.parametrize(
    "text",
    [
        "username,email,frist_name\nada,ada@example.com,Ada\n",  # unknown header
        "email\nada@example.com\n",                              # missing required
    ],
)
async def test_endpoint_rejects_a_malformed_file(session_factory, make_client, text):
    admin = await _admin(session_factory)
    async with make_client(admin) as client:
        resp = await client.post("/api/auth/users/import", files=_upload(text))
    assert resp.status_code == 400, resp.text


async def test_endpoint_rejects_an_over_cap_file(session_factory, make_client):
    from src.auth.users.batch.parsing import MAX_ROWS

    admin = await _admin(session_factory)
    body = "".join(f"u{i},u{i}@example.com\n" for i in range(MAX_ROWS + 1))
    async with make_client(admin) as client:
        resp = await client.post(
            "/api/auth/users/import", files=_upload(f"username,email\n{body}")
        )
    assert resp.status_code == 400
    assert str(MAX_ROWS) in resp.json()["detail"]


async def test_endpoint_rejects_a_file_that_is_not_utf8(session_factory, make_client):
    admin = await _admin(session_factory)
    async with make_client(admin) as client:
        resp = await client.post(
            "/api/auth/users/import",
            files={"file": ("users.csv", b"\xff\xfe\x00bad", "text/csv")},
        )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run --system-certs pytest tests/test_user_batch_import.py -v -k endpoint`
Expected: FAIL with 404 — the route does not exist

- [ ] **Step 3: Write the router**

Create `backend/src/auth/users/batch/routers.py`:

```python
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_superuser
from src.core.exceptions import ValidationError
from src.infrastructure.database import get_db

from ..models import User
from .parsing import parse_file
from .schemas import BatchUserSummary
from .services import import_users

router = APIRouter()


@router.post("/import", response_model=BatchUserSummary)
async def import_users_csv(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
    file: UploadFile = File(...),
):
    """Create many accounts from one CSV.

    Superuser-only, like every other account write -- accounts and group membership
    sit *underneath* the permission model, so whoever can mint them can route around
    it.

    Two kinds of failure, deliberately kept apart. A problem with the *file* -- wrong
    columns, too many rows, not text -- is a 400, because nothing in it can be
    trusted and nothing has been created. A problem with a *row* comes back inside
    the summary, because the rows around it were fine and are already saved.

    **The response carries generated passwords in plaintext.** It is the only place
    they exist; nothing stores them and there is no way to ask again. Do not log it.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file is not valid UTF-8 text; save it as UTF-8 CSV",
        ) from exc

    try:
        parsed = parse_file(text)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return await import_users(db, parsed)
```

- [ ] **Step 4: Wire it into the users router**

In `backend/src/auth/users/routers.py`, add to the imports (after `from . import bin as bin_services`):

```python
from .batch.routers import router as batch_router
```

Then, immediately after the `group_router = APIRouter(...)` definition and before the first `@router.post` decorator, add:

```python
# Included here, before the /{uuid} routes below are registered, so the literal
# /import path is matched ahead of the parameterised ones -- the convention
# list_deleted_users documents. Nothing actually collides today (the only other
# POSTs under /users are "/" and "/{uuid}/restore"), which is the point: it stays
# true when the next one is added.
router.include_router(batch_router)
```

- [ ] **Step 5: Run the whole backend suite**

Run: `cd backend && uv run --system-certs pytest tests/ -q`
Expected: all pass — `test_schema_check.py` and `test_migrations.py` included, since a new router changes the OpenAPI document

- [ ] **Step 6: Commit**

```bash
git add backend/src/auth/users/batch/routers.py backend/src/auth/users/routers.py backend/tests/test_user_batch_import.py
git commit -F - <<'EOF'
Accept a batch-user CSV over HTTP

Superuser-only like every other account write, for the reason the users router
already gives: accounts and group membership sit underneath the permission
model, so whoever can mint them can route around it.

The two kinds of failure stay apart. A bad *file* -- wrong columns, too many
rows, not text -- is a 400, because nothing in it can be trusted and nothing
was created. A bad *row* comes back inside the summary, because the rows
around it were fine and are already saved. Collapsing the two would mean
either a typo'd header reported as a thousand row errors, or one bad email
throwing away 199 good accounts.

The response carries plaintext passwords for the rows the server minted one
for. That is the only place they exist -- nothing persists them and there is
no second chance to read them -- so the handler says so where anyone adding
logging will see it.
EOF
```

---

### Task 4: Frontend types and the credentials file

**Goal:** Regenerate the API types and add the pure function that turns a summary into the one-time credentials CSV.

**Files:**
- Modify: `frontend/openapi.json`, `frontend/src/lib/api-schema.ts` (both regenerated, not hand-edited)
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/src/features/people/credentials.ts`
- Test: `frontend/src/features/people/credentials.test.ts`

**Acceptance Criteria:**
- [ ] `npm run gen:api` regenerates cleanly and `BatchUserSummary` appears in `api-schema.ts`
- [ ] `types.ts` exports `BatchUserSummary`, `CreatedUser` and `BatchUserError` as response types
- [ ] `credentialsCsv` returns null when no password was generated
- [ ] Rows whose password the admin supplied are left out of the file
- [ ] A username containing a comma or quote is escaped by `toCsv`

**Verify:** `cd frontend && npm test -- credentials` → passes; `npm run build` → no type errors

**Steps:**

- [ ] **Step 1: Regenerate the API types**

Run: `cd frontend && npm run gen:api`
Expected: `openapi.json` and `src/lib/api-schema.ts` both change; `BatchUserSummary`, `CreatedUser`, `BatchUserError` appear in the schema file. This requires the backend from Task 3 to be importable — it runs `python -m src.infrastructure.export_openapi`, not a live server.

- [ ] **Step 2: Export the new types**

In `frontend/src/lib/types.ts`, alongside the other `Response<Schemas[...]>` aliases, add:

```typescript
/** The result of a batch user import. `generated_password` is present exactly once
 *  — the server does not store it and cannot be asked again. */
export type BatchUserSummary = Response<Schemas["BatchUserSummary"]>;
export type CreatedUser = Response<Schemas["CreatedUser"]>;
export type BatchUserError = Response<Schemas["BatchUserError"]>;
```

- [ ] **Step 3: Write the failing test**

Create `frontend/src/features/people/credentials.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import type { CreatedUser } from "../../lib/types";
import { credentialsCsv } from "./credentials";

function created(over: Partial<CreatedUser> = {}): CreatedUser {
  return {
    row: 2,
    uuid: "u1",
    username: "ada",
    generated_password: "s3cret",
    ...over,
  } as CreatedUser;
}

describe("credentialsCsv", () => {
  it("writes a row per generated password", () => {
    const csv = credentialsCsv([
      created(),
      created({ username: "grace", generated_password: "hunter2" }),
    ]);
    expect(csv).toBe("username,password\r\nada,s3cret\r\ngrace,hunter2");
  });

  it("leaves out rows whose password the admin supplied", () => {
    // The server never sends those back, so there is nothing to hand over.
    const csv = credentialsCsv([
      created(),
      created({ username: "grace", generated_password: null }),
    ]);
    expect(csv).toBe("username,password\r\nada,s3cret");
  });

  it("returns null when nothing was generated", () => {
    // What the dialog keys the download button off.
    expect(credentialsCsv([created({ generated_password: null })])).toBeNull();
    expect(credentialsCsv([])).toBeNull();
  });

  it("escapes a username that would otherwise break the file", () => {
    const csv = credentialsCsv([created({ username: 'a,b"c' })]);
    expect(csv).toBe('username,password\r\n"a,b""c",s3cret');
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd frontend && npm test -- credentials`
Expected: FAIL — `Failed to resolve import "./credentials"`

- [ ] **Step 5: Write the helper**

Create `frontend/src/features/people/credentials.ts`:

```typescript
import { toCsv } from "../../lib/csv";
import type { CreatedUser } from "../../lib/types";

/**
 * The one-time credentials file, for the accounts whose passwords the server minted.
 *
 * Rows the admin supplied a password for are left out: they already know it, and the
 * server never sends it back. Returns null when there is nothing to hand over, which
 * is what the dialog keys its download button off — an empty file with a header row
 * would look like a bug.
 *
 * This is the only place these passwords are ever written down. They are not stored
 * server-side and cannot be requested again; a lost one is a password reset.
 */
export function credentialsCsv(created: CreatedUser[]): string | null {
  const rows = created
    .filter((user) => !!user.generated_password)
    .map((user) => [user.username, user.generated_password as string]);
  return rows.length ? toCsv(["username", "password"], rows) : null;
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npm test -- credentials`
Expected: 4 passing

- [ ] **Step 7: Commit**

```bash
git add frontend/openapi.json frontend/src/lib/api-schema.ts frontend/src/lib/types.ts frontend/src/features/people/credentials.ts frontend/src/features/people/credentials.test.ts
git commit -F - <<'EOF'
Build the one-time credentials file from an import summary

Passwords the server minted come back in the import response and nowhere
else, so turning them into something the admin can keep is the last step of
the feature rather than an afterthought. Rows where the admin supplied the
password are left out: the server never echoes those, and a file listing
half of them blank invites the reading that the other half failed.

Null rather than an empty file when nothing was generated. The dialog keys
its download button off this, and a lone header row downloaded from a button
that should not have been there looks exactly like a bug.
EOF
```

---

### Task 5: The dialog

**Goal:** A superuser can download the template, upload a file, and see what happened — including getting the credentials out.

**Files:**
- Create: `frontend/src/features/people/BatchUserDialog.tsx`
- Modify: `frontend/src/features/people/PeoplePage.tsx`

**Acceptance Criteria:**
- [ ] An "Import users" button sits beside "Add user", superuser-only
- [ ] "Download template" produces a CSV with all twelve headers and one example row
- [ ] Uploading posts to `/auth/users/import` as multipart and renders the summary
- [ ] Row errors list as `Row N: message`, scrollable
- [ ] A "Download credentials" button appears only when a password was generated, with copy saying they cannot be retrieved again
- [ ] A warning appears when `superusers_created > 0`
- [ ] Success invalidates the `users` and `people` query keys
- [ ] `npm run build` reports no type errors

**Verify:** `cd frontend && npm run build` → no errors; then by hand: log in as a superuser, People & Groups → Import users, download the template, add two rows, upload, confirm both accounts appear in the Users list and the credentials file downloads

**Steps:**

- [ ] **Step 1: Write the dialog**

Create `frontend/src/features/people/BatchUserDialog.tsx`:

```typescript
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { FileUpload, type FileUploadHandlerEvent } from "primereact/fileupload";
import { Message } from "primereact/message";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { toCsv } from "../../lib/csv";
import { downloadFile } from "../../lib/download";
import { useToast } from "../../lib/toast";
import type { BatchUserSummary } from "../../lib/types";
import { credentialsCsv } from "./credentials";

interface Props {
  visible: boolean;
  onHide: () => void;
}

// The twelve columns the server accepts, in the order the template writes them.
// Kept in step with KNOWN_HEADERS in backend/src/auth/users/batch/parsing.py by
// hand: a header this file invents is a 400, which is the loud kind of wrong.
const HEADERS = [
  "username",
  "email",
  "password",
  "is_active",
  "is_superuser",
  "groups",
  "name_prefix",
  "first_name",
  "middle_name",
  "last_name",
  "name_suffix",
  "birth_date",
];

// One filled-in row, because a bare header line does not convey that `groups` is
// semicolon-separated or that `birth_date` wants YYYY-MM-DD.
const EXAMPLE = [
  "alovelace",
  "ada@example.com",
  "",
  "true",
  "false",
  "Teachers;Year 9",
  "Dr",
  "Ada",
  "King",
  "Lovelace",
  "",
  "1815-12-10",
];

/**
 * Create many accounts from one CSV.
 *
 * The counterpart to `UserDialog` for a roster rather than a person. Same two
 * records behind it — a `User` and an optional `Person` — and the same seam hidden:
 * fill the name columns and the row gets a profile, leave them blank and it does
 * not.
 *
 * Bad rows are skipped and reported rather than sinking the file, matching what list
 * import already does. A bad *file* — wrong columns, too many rows — is refused
 * whole, because nothing in it can be trusted.
 *
 * **Passwords the server generated are in the response and nowhere else.** They are
 * not stored and cannot be asked for again, so the credentials download is the only
 * chance to keep them; the panel says so rather than letting anyone find out later.
 */
export function BatchUserDialog({ visible, onHide }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchUserSummary | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  function handleTemplate() {
    downloadFile("users-template.csv", toCsv(HEADERS, [EXAMPLE]), "text/csv");
  }

  function handleClose() {
    // Deliberately dropped, not cached: the generated passwords in here should not
    // outlive the dialog that warned they were one-time.
    setResult(null);
    setFileError(null);
    onHide();
  }

  async function handleUpload(event: FileUploadHandlerEvent) {
    const file = event.files?.[0];
    event.options.clear();
    if (!file) return;
    setBusy(true);
    setResult(null);
    setFileError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const summary = await api.postForm<BatchUserSummary>(
        "/auth/users/import",
        form,
      );
      setResult(summary);
      queryClient.invalidateQueries({ queryKey: ["users"] });
      queryClient.invalidateQueries({ queryKey: ["people"] });
      const skipped = summary.errors.length;
      if (skipped === 0) {
        toast.success(
          "Import complete",
          `${summary.created.length} account(s) created`,
        );
      } else {
        toast.warn(
          "Import finished with errors",
          `${summary.created.length} created, ${skipped} skipped`,
        );
      }
    } catch (err) {
      // A file-level refusal, not a row problem: there is no summary to show.
      setFileError(err instanceof ApiError ? err.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  const credentials = result ? credentialsCsv(result.created) : null;

  return (
    <AppDialog
      header="Import users"
      visible={visible}
      onHide={handleClose}
      style={{ width: 560 }}
      footer={
        <>
          <Button
            label="Download template"
            icon="pi pi-file"
            text
            onClick={handleTemplate}
          />
          <Button label="Close" text onClick={handleClose} />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="field-hint">
          Upload a .csv whose headers are the account and profile fields.{" "}
          <strong>username</strong> and <strong>email</strong> are required; leave{" "}
          <strong>password</strong> blank and one is generated. Fill the name columns
          to give the account a profile, or leave them blank for an account without
          one. <strong>groups</strong> takes existing group names separated by
          semicolons. Each row is validated on the server and a bad one is skipped,
          not fatal. Not sure of the shape? Download the template.
        </p>

        <FileUpload
          mode="basic"
          customUpload
          auto
          accept=".csv"
          chooseLabel="Choose file"
          uploadHandler={handleUpload}
          disabled={busy}
        />

        {fileError && <Message severity="error" text={fileError} />}

        {result && (
          <Message
            severity={result.errors.length === 0 ? "success" : "warn"}
            text={`Created ${result.created.length} account(s)${
              result.errors.length ? `, ${result.errors.length} skipped` : ""
            }.`}
          />
        )}

        {result && result.superusers_created > 0 && (
          <Message
            severity="warn"
            text={`${result.superusers_created} of these accounts were made superusers.`}
          />
        )}

        {credentials && (
          <div className="flex flex-col gap-2">
            <Message
              severity="warn"
              text="Generated passwords are shown once. Download them now — closing this dialog loses them, and a lost one has to be reset from Edit user."
            />
            <Button
              label="Download credentials"
              icon="pi pi-download"
              onClick={() =>
                downloadFile("users-credentials.csv", credentials, "text/csv")
              }
            />
          </div>
        )}

        {result && result.errors.length > 0 && (
          <ul className="text-sm text-danger m-0 pl-4 max-h-40 overflow-auto">
            {result.errors.map((error, index) => (
              <li key={index}>
                {error.row ? `Row ${error.row}: ` : ""}
                {error.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppDialog>
  );
}
```

- [ ] **Step 2: Wire it into the People page**

In `frontend/src/features/people/PeoplePage.tsx`:

Add to the imports beside the other dialogs:

```typescript
import { BatchUserDialog } from "./BatchUserDialog";
```

Add the state beside `showDeletedUsers`:

```typescript
const [showBatchUsers, setShowBatchUsers] = useState(false);
```

In the Users section header, add a button between "Deleted" and "Add user":

```typescript
              <Button
                label="Import users"
                icon="pi pi-upload"
                text
                onClick={() => setShowBatchUsers(true)}
              />
```

And render the dialog beside `DeletedPrincipalsDialog`:

```typescript
      <BatchUserDialog
        visible={showBatchUsers}
        onHide={() => setShowBatchUsers(false)}
      />
```

- [ ] **Step 3: Verify it compiles**

Run: `cd frontend && npm run build`
Expected: no type errors (the script runs `tsc --noEmit` before building)

- [ ] **Step 4: Run every test**

Run: `cd frontend && npm test` and `cd backend && uv run --system-certs pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Check it by hand**

Start both servers (backend `uv run --system-certs uvicorn main:create_app --factory --port 5000`, frontend `npm run dev`, open `http://localhost:5173`). As a superuser:

1. People & Groups → **Import users** → **Download template** — confirm twelve headers and the example row
2. Edit it to two rows, one with names and one without, leaving `password` blank
3. Upload — confirm "Created 2 account(s)", the credentials warning, and that the file downloads with two rows
4. Confirm both accounts appear in the Users list, one showing its full name
5. Upload a file with a `frist_name` header — confirm a single 400 message, not a list of row errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/people/BatchUserDialog.tsx frontend/src/features/people/PeoplePage.tsx
git commit -F - <<'EOF'
Import a roster from the People page

The counterpart to the Add user dialog for a roster rather than a person, and
it hides the same seam: fill the name columns and the row gets a profile,
leave them blank and it does not. The template carries one filled-in example
row, because a bare header line does not convey that groups are separated by
semicolons or that a birth date wants YYYY-MM-DD -- the two things a
hand-written file gets wrong.

File-level refusals and row-level errors are rendered differently on purpose,
since they call for different actions: a 400 means fix the file and try
again, while a list of skipped rows means the rest of the import already
happened.

The credentials download is the only chance to keep the passwords the server
generated -- they are not stored and cannot be requested again -- so the panel
says so next to the button rather than leaving it to be discovered. Closing
the dialog drops the summary rather than caching it, so the warning stays
true.
EOF
```

---

## Self-Review

**Spec coverage:** every section of the design maps to a task — CSV contract and template → Tasks 1 and 5; backend package, pipeline order, response shape → Tasks 1–3; frontend → Tasks 4–5; the test list → Tasks 1–3 and 4. The design's "What this does not do" items stay undone.

**Placeholder scan:** no TBD/TODO; every code step carries real code.

**Type consistency:** `BatchUserRow`, `BatchUserError`, `CreatedUser`, `BatchUserSummary`, `ParsedFile`, `parse_file`, `check_headers`, `resolve_groups`, `import_users`, `credentialsCsv`, `MAX_ROWS`, `KNOWN_HEADERS` are spelled identically everywhere they appear. `HEADERS` in the dialog duplicates `KNOWN_HEADERS` across the language boundary — the only hand-maintained pair, flagged in a comment where it lives.
