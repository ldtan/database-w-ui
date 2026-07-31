"""The file-format rules, with no database in sight.

Everything here is pure: `parse_file` settles what can be known from the upload
alone, so the import service only ever sees well-formed rows. Keeping these tests
free of Postgres is the point -- they are the bulk of the behaviour and they run in
milliseconds.
"""
import pytest

from src.auth.users.batch.parsing import MAX_ROWS, parse_file
from src.core.exceptions import ValidationError

PERSON_HEADER = "name_prefix,first_name,middle_name,last_name,name_suffix,birth_date"


def test_minimal_file_parses():
    parsed = parse_file("username,email\nada,ada@example.com\n")
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
        "username,email,groups\nada,ada@example.com,Teachers; Year 9 ;;\n"
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
    assert (person.name_prefix, person.first_name, person.last_name) == (
        "Dr", "Ada", "Lovelace",
    )
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
