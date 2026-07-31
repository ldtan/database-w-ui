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
    summary = await _run(db, f"{HEADER}\nada,ada@example.com,hunter22,,,\n")
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
    summary = await _run(db, f"{HEADER}\nada,ada@example.com,,,,Nonexistent\n")
    assert summary.created == []
    assert "Nonexistent" in summary.errors[0].message
    assert await UserService.get_by_username(db, "ada") is None


async def test_known_groups_are_attached(db):
    await UserGroupService.create(db, UserGroupCreate(name="Teachers"))
    await UserGroupService.create(db, UserGroupCreate(name="Year 9"))

    summary = await _run(db, f"{HEADER}\nada,ada@example.com,,,,Teachers;Year 9\n")
    assert summary.errors == []
    ada = await UserService.get_by_uuid(db, summary.created[0].uuid)
    assert sorted(group.name for group in ada.groups) == ["Teachers", "Year 9"]


async def test_superuser_promotion_is_applied_and_counted(db):
    summary = await _run(db, f"{HEADER}\nroot,root@example.com,,,true,\n")
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
