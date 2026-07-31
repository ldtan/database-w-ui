"""`slugify_name` and the group backfill's dedupe, tested away from Postgres.

The migration that gives `user_groups` a slug `name` (b3f9d47c1a06) rewrites a column
that two wire formats resolve against, and it runs exactly once against real data. Its
risk is concentrated in a pure string transform, so that transform is tested directly
rather than only through whatever names a fixture happens to use.

The migration carries a frozen copy of the transform rather than importing this one
(see its docstring), so the last test here pins the two together: a change to
`slugify_name` that silently diverges from the migration is the failure this catches.
"""
import pytest

from src.core.utils import slugify_name

# Imported by path, because a migration filename is not an identifier.
import importlib.util
from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "b3f9d47c1a06_group_name_becomes_a_slug.py"
)
_spec = importlib.util.spec_from_file_location("_group_slug_migration", _MIGRATION_PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Teachers", "teachers"),
        ("Year 9", "year_9"),
        # The apostrophe is punctuation like any other, so it becomes a separator
        # rather than disappearing.
        ("Registrar's Office", "registrar_s_office"),
        ("Department Heads", "department_heads"),
        # A run of separators collapses instead of producing empty segments.
        ("Year 10  &  11 Form Tutors", "year_10_11_form_tutors"),
        ("  padded  ", "padded"),
        ("--leading-and-trailing--", "leading_and_trailing"),
        ("already_a_slug", "already_a_slug"),
        ("MiXeD CaSe", "mixed_case"),
        # NameSlug must start with a letter, so a leading digit is prefixed.
        ("7th Grade", "x_7th_grade"),
        ("2026 Intake", "x_2026_intake"),
        # Nothing usable left: the fallback keeps the result a valid slug.
        ("!!!", "item"),
        ("", "item"),
        ("   ", "item"),
    ],
)
def test_slugify_name(raw, expected):
    assert slugify_name(raw) == expected


def test_the_result_always_satisfies_nameslug():
    """Whatever goes in, what comes out is something the API would accept."""
    import re

    from src.core.schemas import NameSlug  # noqa: F401  (documents the pattern's home)

    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for raw in [
        "Teachers", "7th Grade", "!!!", "  ", "É", "___", "9", "a" * 200,
        "Year 10 & 11", "Registrar's Office", "-", "1_2_3",
    ]:
        slug = slugify_name(raw)
        assert pattern.match(slug), f"{raw!r} produced {slug!r}"
        assert len(slug) <= 63


def test_truncation_respects_max_length_and_leaves_no_trailing_underscore():
    raw = " ".join(["word"] * 40)  # far past 63 characters
    slug = slugify_name(raw)
    assert len(slug) <= 63
    assert not slug.endswith("_")

    short = slugify_name("a very long group name here", max_length=10)
    assert len(short) <= 10
    assert not short.endswith("_")


def test_a_non_ascii_name_still_produces_a_valid_slug():
    """Not transliteration — the letters are dropped, not romanised. What matters is
    that the result is still addressable rather than that it is still readable; the
    display name keeps the readable version."""
    assert slugify_name("Café Society") == "caf_society"
    assert slugify_name("日本語") == "item"


# ------------------------------------------------------------ migration parity


def test_the_migration_copy_matches_the_application_copy():
    """The migration freezes its own copy on purpose. It may lag this one only
    deliberately — never by accident, which is what this catches."""
    for raw in [
        "Teachers", "Year 9", "Registrar's Office", "7th Grade", "!!!", "",
        "Year 10  &  11 Form Tutors", "MiXeD CaSe", "Café Society", "a" * 100,
    ]:
        assert migration._slugify(raw) == slugify_name(raw), raw


def test_colliding_names_are_deduplicated_in_id_order():
    """Two distinct free-text names can slugify identically, and `name` is unique."""
    taken: set[str] = set()
    results = []
    for group_id, name in enumerate(
        ["Grade-7 Teachers", "Grade 7 teachers", "grade 7 TEACHERS"], start=1
    ):
        slug = migration._dedupe(migration._slugify(name), taken, group_id, name)
        taken.add(slug)
        results.append(slug)

    # The oldest row keeps the clean slug, so the outcome does not depend on the
    # order Postgres happened to return rows in.
    assert results == ["grade_7_teachers", "grade_7_teachers_2", "grade_7_teachers_3"]


def test_dedupe_leaves_an_uncontested_slug_alone():
    assert migration._dedupe("teachers", set(), 1, "Teachers") == "teachers"


def test_a_deduplicated_slug_still_fits_the_column():
    """The suffix is why the migration slugifies to less than the full width."""
    long_name = "A" + " very" * 40 + " long group"
    taken: set[str] = set()
    for group_id in range(1, 12):
        slug = migration._dedupe(
            migration._slugify(long_name, migration.MAX_LENGTH - migration._SUFFIX_ROOM),
            taken,
            group_id,
            long_name,
        )
        taken.add(slug)
        assert len(slug) <= migration.MAX_LENGTH


def test_dedupe_gives_up_loudly_rather_than_looping():
    taken = {"team"} | {f"team_{n}" for n in range(2, 1000)}
    with pytest.raises(RuntimeError, match="Could not find a free name"):
        migration._dedupe("team", taken, 1, "Team")
