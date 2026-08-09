"""Westbrook Academy — a whole school's records, built into an empty database.

    uv run python -m src.demo.school
    uv run python -m src.demo.school --password letmein

What it builds: 16 staff accounts with person profiles, 6 groups, 5 workspaces,
17 lists spanning every column type, ~740 records, and the views, forms, metrics
and permission grants that make them worth looking at. Safe to re-run — see
`_builders` for what "already there" means per resource.

Three properties of the app, not of the school, shape the scenario. They are worth
knowing before editing it, because each one is a wall you would otherwise walk into.

**A lookup across owners needs `lookup` on the target, from whoever writes the
cell.** This used to be a hard wall: the column could be *created* against any list
its author could `lookup`, its labels resolved fine, and then every cell write was
refused with "points at a missing target list", because the write re-resolved the
target scoped to the owner of the list holding the column. That owner scope is gone
(`ListItemService._lookup_target_id`), replaced by the check the read path already
made — so the shape below is no longer forced, only tidy. The registrar owns the
four workspaces whose lists reference each other, and `operations` — the one estate
that stands alone — is the IT admin's; cross-owner access is expressed the way the
permission model intends, with grants. What still matters when editing: the account
writing a cross-owner lookup cell must hold `lookup` on the target, which a grant
of `view` also confers. `staff_directory.department` records where the old wall
stood; see the note at that column.

**Nothing links to a calculated column.** A calculated cell is derived on read and
stores nothing, so a lookup pointing at one resolves to a blank label and a sort on
one does nothing. Every list that is linked to therefore has a *stored* primary
column, and the calculated columns here are the derived-for-display fields they are
meant to be — including two (`students.honour_points`, `enrollments.pass_points`)
that exist to be totalled, since a metric may aggregate a calculated column and
that is the cleanest way to ask "how many of these passed?".

**Generation runs even when writing is skipped.** Every record here is generated
from one seeded RNG, so two runs produce the same school; the generators are
therefore always executed and only the *write* is skipped when a list already has
rows. That is what keeps `w.rows["students"][4]` meaning the same learner on the
second run, which is what the lists that link to it depend on.
"""
import argparse
import asyncio
import random
from datetime import date, datetime, timedelta, timezone

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.auth.seed import ensure_system_groups
from src.auth.users.models import AUTHENTICATED_USERS_NAME
from src.auth.users.services import UserGroupService
from src.infrastructure import database as db_module
from src.lists.columns.schemas import ColumnConfig, ColumnUpdate
from src.lists.columns.services import ColumnService
from src.lists.forms.enums import FormType
from src.lists.items.schemas import ListItemUpdate
from src.lists.items.services import ListItemService
from src.lists.services import ListService
from src.lists.views.services import ViewService
from src.permissions.enums import Action, ResourceType

from ._builders import (
    Report,
    bin_records,
    ensure_column,
    ensure_form,
    ensure_group,
    ensure_list,
    ensure_metric,
    ensure_person,
    ensure_user,
    ensure_view,
    ensure_workspace,
    grant,
    list_exists,
    list_owned_by,
    seed_records,
    set_primary_column,
    set_settings,
    set_unique_permissions,
)

# One password for every seeded account. A demo tenant is not a secret, and one the
# operator has to look up account by account is one nobody logs into.
DEFAULT_PASSWORD = "westbrook2026"

# Fixed, so two runs produce the same school. Every random choice below draws from
# this one generator, which means the order they are drawn in is part of the
# output — reordering the generators reshuffles the data.
RNG_SEED = 20260729

# The school year the data sits in. `TODAY` is when the scenario is "now": records
# are dated behind it, so nothing lands in the future (which a birth date refuses
# outright, and which would make every "this term" view read as empty).
YEAR_START = date(2026, 6, 1)
TODAY = date(2026, 7, 29)

# The role presets, spelled out. `view` already confers `lookup` and `manage`
# confers everything — see permissions/enums.
VIEWER = [Action.view]
EDITOR = [Action.view, Action.create, Action.edit, Action.delete]
MANAGER = [Action.manage]
LOOKUP_ONLY = [Action.lookup]


# -- the people ------------------------------------------------------------

# username, prefix, first, middle, last, suffix, born, role
STAFF = [
    ("m.reyes", "Dr", "Marisol", "Ibañez", "Reyes", None, (1974, 3, 12), "Principal"),
    ("s.villanueva", "Ms", "Soledad", None, "Villanueva", None, (1981, 7, 4), "Registrar"),
    ("d.okafor", "Mr", "Daniel", "Chinedu", "Okafor", None, (1986, 11, 23), "Guidance counsellor"),
    ("n.santos", "Ms", "Nerissa", None, "Santos", None, (1990, 2, 17), "School nurse"),
    ("f.aquino", "Mr", "Fidel", "Ramon", "Aquino", None, (1979, 9, 8), "Librarian"),
    ("b.lim", "Ms", "Beatriz", None, "Lim", None, (1983, 5, 30), "Finance officer"),
    ("c.delacruz", "Dr", "Cristina", "Perez", "Dela Cruz", None, (1977, 12, 2), "Head, Sciences"),
    ("l.tan", "Mr", "Lorenzo", None, "Tan", "Jr", (1988, 4, 19), "Mathematics teacher"),
    ("a.mercado", "Ms", "Althea", "Rose", "Mercado", None, (1992, 8, 25), "Science teacher"),
    ("j.cruz", "Mr", "Joaquin", None, "Cruz", None, (1985, 1, 14), "English teacher"),
    ("p.navarro", "Ms", "Paulina", "Mae", "Navarro", None, (1991, 6, 6), "Filipino teacher"),
    ("r.ibarra", "Mr", "Rodel", None, "Ibarra", None, (1987, 10, 29), "PE teacher"),
    ("k.bautista", "Ms", "Katrina", None, "Bautista", None, (1994, 3, 3), "Arts teacher"),
    ("e.gutierrez", "Mr", "Emilio", "Andres", "Gutierrez", "III", (1980, 7, 21), "Social Studies teacher"),
    ("t.reyes", "Mr", "Teodoro", None, "Reyes", None, (1996, 9, 15), "Substitute teacher"),
]

# Keyed by the group's slug `name`; the first element of each value is the
# `display_name`.
#
# Each key is exactly what `slugify_name` makes of the display name beside it, which
# is not cosmetic: the migration that gave groups a slug (b3f9d47c1a06) derived one
# the same way, so a tenant seeded before that change is *found* by `ensure_group`
# rather than duplicated beside itself on the next run. Hence "registrar_s_office"
# rather than the tidier "registrar" -- the tidier one would not match.
GROUPS = {
    "teachers": (
        "Teachers",
        "Everyone with a teaching load this school year.",
        ["l.tan", "a.mercado", "j.cruz", "p.navarro", "r.ibarra", "k.bautista",
         "e.gutierrez", "c.delacruz", "t.reyes"],
    ),
    "administration": (
        "Administration",
        "Principal's office, registrar and finance.",
        ["m.reyes", "s.villanueva", "b.lim", "admin"],
    ),
    "registrar_s_office": (
        "Registrar's Office",
        "Keeps the student record: admissions, enrolment and transcripts.",
        ["s.villanueva", "m.reyes"],
    ),
    "department_heads": (
        "Department Heads",
        "Curriculum and teaching-load review.",
        ["c.delacruz", "l.tan", "j.cruz"],
    ),
    "student_support": (
        "Student Support",
        "Guidance and the clinic. Sees student welfare records.",
        ["d.okafor", "n.santos"],
    ),
    "library_staff": (
        "Library Staff",
        "Runs the catalogue and circulation.",
        ["f.aquino"],
    ),
}

# Preferences follow the account rather than the browser, so the demo shows people
# who have actually set them.
PREFERENCES = {
    "m.reyes": {"theme": "luxury", "mode": "light", "timezone": "Asia/Manila"},
    "s.villanueva": {"theme": "nordic", "mode": "light", "timezone": "Asia/Manila"},
    "d.okafor": {"theme": "modern", "mode": "dark", "timezone": "Asia/Manila"},
    "l.tan": {"theme": "brutalist", "mode": "dark", "timezone": "Asia/Manila"},
    "f.aquino": {"theme": "nordic", "mode": "dark", "timezone": "Asia/Manila"},
    "n.santos": {"theme": "modern", "mode": "light", "timezone": "Asia/Manila"},
}

# Which department each member of staff belongs to in the operations directory.
DEPARTMENT_OF = {
    "l.tan": "Mathematics", "c.delacruz": "Sciences", "a.mercado": "Sciences",
    "j.cruz": "English", "p.navarro": "Filipino", "e.gutierrez": "Social Studies",
    "r.ibarra": "Physical Education", "k.bautista": "Arts and Design",
    "t.reyes": "Mathematics",
}

# Slugs, not display names: this fills a `group` cell, and a group cell resolves a
# written value against `UserGroup.name` (see ListItemService._validate_principal) so
# that a file our own export produced imports back cleanly.
GROUP_OF = {
    "m.reyes": "administration", "s.villanueva": "registrar_s_office",
    "b.lim": "administration", "d.okafor": "student_support",
    "n.santos": "student_support", "f.aquino": "library_staff",
    "c.delacruz": "department_heads", "admin": "administration",
}


# -- pools the generated records draw from ---------------------------------

GIVEN_NAMES = [
    "Miguel", "Sofia", "Gabriel", "Isabella", "Rafael", "Andrea", "Joshua",
    "Camille", "Nathaniel", "Bianca", "Enrique", "Danica", "Paolo", "Kristine",
    "Marco", "Trisha", "Vincent", "Angelica", "Julian", "Hazel", "Kevin",
    "Patricia", "Emmanuel", "Yvonne", "Lorenzo", "Charmaine", "Dominic",
    "Rowena", "Sebastian", "Katrina", "Alfonso", "Michelle", "Gerald",
    "Denise", "Rommel", "Jasmine", "Arnel", "Faith", "Noel", "Precious",
]

SURNAMES = [
    "Santos", "Reyes", "Cruz", "Bautista", "Ocampo", "Garcia", "Mendoza",
    "Torres", "Castillo", "Ramos", "Aquino", "Delos Santos", "Villanueva",
    "Navarro", "Mercado", "Salazar", "Domingo", "Fernandez", "Gonzales",
    "Rivera", "Alvarez", "Panganiban", "Dizon", "Lagman", "Sarmiento",
    "Tolentino", "Yap", "Ilagan", "Marasigan", "Bermudez",
]

# name, code, head, remit
DEPARTMENTS = [
    ("Mathematics", "MATH", "l.tan", "Algebra through calculus, plus the competition team."),
    ("Sciences", "SCI", "c.delacruz", "Biology, chemistry and physics. Runs both laboratories."),
    ("English", "ENG", "j.cruz", "Language, literature and the school paper."),
    ("Filipino", "FIL", "p.navarro", "Wika at panitikan."),
    ("Social Studies", "SOSC", "e.gutierrez", "Araling panlipunan, economics and civics."),
    ("Physical Education", "PE", "r.ibarra", "PE, health and the varsity programme."),
    ("Arts and Design", "ARTS", "k.bautista", "Visual arts, music and the annual production."),
]

BUILDINGS = ["Main Building", "Annex", "Science Wing", "Gymnasium"]

# code, building, floor, seats, department, projector
ROOMS = [
    ("MB-101", "Main Building", 1, 40, "English", True),
    ("MB-102", "Main Building", 1, 40, "English", True),
    ("MB-103", "Main Building", 1, 38, "Filipino", False),
    ("MB-201", "Main Building", 2, 42, "Mathematics", True),
    ("MB-202", "Main Building", 2, 42, "Mathematics", True),
    ("MB-203", "Main Building", 2, 36, "Mathematics", False),
    ("MB-301", "Main Building", 3, 45, "Social Studies", True),
    ("MB-302", "Main Building", 3, 45, "Social Studies", False),
    ("AN-101", "Annex", 1, 30, "Arts and Design", True),
    ("AN-102", "Annex", 1, 25, "Arts and Design", False),
    ("AN-201", "Annex", 2, 32, "Filipino", False),
    ("AN-202", "Annex", 2, 32, "English", False),
    ("SW-101", "Science Wing", 1, 35, "Sciences", True),
    ("SW-102", "Science Wing", 1, 35, "Sciences", True),
    ("SW-201", "Science Wing", 2, 28, "Sciences", True),
    ("SW-202", "Science Wing", 2, 28, "Sciences", False),
    ("GY-001", "Gymnasium", 1, 60, "Physical Education", False),
    ("GY-002", "Gymnasium", 1, 50, "Physical Education", False),
]

GRADE_LEVELS = [
    ("Grade 7", 7, "Junior High"),
    ("Grade 8", 8, "Junior High"),
    ("Grade 9", 9, "Junior High"),
    ("Grade 10", 10, "Junior High"),
    ("Grade 11", 11, "Senior High"),
    ("Grade 12", 12, "Senior High"),
]

# code, title, department, year level, term, units, teacher
COURSES = [
    ("ALG1", "Algebra I", "Mathematics", "Grade 7", "Full Year", 3, "l.tan"),
    ("GEOM", "Geometry", "Mathematics", "Grade 8", "Full Year", 3, "l.tan"),
    ("STAT", "Statistics and Probability", "Mathematics", "Grade 11", "First Semester", 3, "l.tan"),
    ("BIO1", "General Biology", "Sciences", "Grade 9", "Full Year", 4, "a.mercado"),
    ("CHEM", "General Chemistry", "Sciences", "Grade 10", "Full Year", 4, "c.delacruz"),
    ("PHYS", "General Physics", "Sciences", "Grade 12", "Second Semester", 4, "c.delacruz"),
    ("ENG7", "English 7", "English", "Grade 7", "Full Year", 3, "j.cruz"),
    ("LIT1", "World Literature", "English", "Grade 10", "First Semester", 3, "j.cruz"),
    ("FIL7", "Filipino 7", "Filipino", "Grade 7", "Full Year", 3, "p.navarro"),
    ("PANIT", "Panitikang Filipino", "Filipino", "Grade 11", "Second Semester", 3, "p.navarro"),
    ("AP8", "Araling Panlipunan 8", "Social Studies", "Grade 8", "Full Year", 3, "e.gutierrez"),
    ("ECON", "Applied Economics", "Social Studies", "Grade 12", "First Semester", 3, "e.gutierrez"),
    ("PE9", "Physical Education 9", "Physical Education", "Grade 9", "Full Year", 2, "r.ibarra"),
    ("ART10", "Art Appreciation", "Arts and Design", "Grade 10", "Second Semester", 2, "k.bautista"),
]

# title, author, genre
BOOKS = [
    ("Noli Me Tangere", "José Rizal", "Filipiniana"),
    ("El Filibusterismo", "José Rizal", "Filipiniana"),
    ("Florante at Laura", "Francisco Balagtas", "Filipiniana"),
    ("Dekada '70", "Lualhati Bautista", "Filipiniana"),
    ("Po-on: A Novel", "F. Sionil José", "Filipiniana"),
    ("Ilustrado", "Miguel Syjuco", "Fiction"),
    ("Smaller and Smaller Circles", "F. H. Batacan", "Fiction"),
    ("To Kill a Mockingbird", "Harper Lee", "Fiction"),
    ("Nineteen Eighty-Four", "George Orwell", "Fiction"),
    ("Animal Farm", "George Orwell", "Fiction"),
    ("Pride and Prejudice", "Jane Austen", "Fiction"),
    ("Jane Eyre", "Charlotte Brontë", "Fiction"),
    ("Great Expectations", "Charles Dickens", "Fiction"),
    ("The Old Man and the Sea", "Ernest Hemingway", "Fiction"),
    ("Things Fall Apart", "Chinua Achebe", "Fiction"),
    ("One Hundred Years of Solitude", "Gabriel García Márquez", "Fiction"),
    ("The Kite Runner", "Khaled Hosseini", "Fiction"),
    ("Never Let Me Go", "Kazuo Ishiguro", "Fiction"),
    ("The Book Thief", "Markus Zusak", "Fiction"),
    ("Life of Pi", "Yann Martel", "Fiction"),
    ("The Hobbit", "J. R. R. Tolkien", "Fiction"),
    ("A Wizard of Earthsea", "Ursula K. Le Guin", "Fiction"),
    ("The Left Hand of Darkness", "Ursula K. Le Guin", "Fiction"),
    ("Frankenstein", "Mary Shelley", "Fiction"),
    ("The Complete Sherlock Holmes", "Arthur Conan Doyle", "Fiction"),
    ("A Brief History of Time", "Stephen Hawking", "Science"),
    ("Cosmos", "Carl Sagan", "Science"),
    ("The Selfish Gene", "Richard Dawkins", "Science"),
    ("Silent Spring", "Rachel Carson", "Science"),
    ("The Double Helix", "James D. Watson", "Science"),
    ("Molecular Biology of the Cell", "Bruce Alberts", "Reference"),
    ("Campbell Biology", "Lisa A. Urry", "Reference"),
    ("University Physics", "Hugh D. Young", "Reference"),
    ("Chemistry: The Central Science", "Theodore Brown", "Reference"),
    ("Calculus: Early Transcendentals", "James Stewart", "Reference"),
    ("Introduction to Statistics", "Ronald Walpole", "Reference"),
    ("The Elements of Style", "William Strunk Jr.", "Reference"),
    ("Merriam-Webster Collegiate Dictionary", "Merriam-Webster", "Reference"),
    ("Guns, Germs, and Steel", "Jared Diamond", "History"),
    ("Sapiens: A Brief History of Humankind", "Yuval Noah Harari", "History"),
    ("A People's History of the United States", "Howard Zinn", "History"),
    ("The Wealth of Nations", "Adam Smith", "History"),
    ("Rizal Without the Overcoat", "Ambeth Ocampo", "History"),
    ("Looking Back", "Ambeth Ocampo", "History"),
    ("The Diary of a Young Girl", "Anne Frank", "Biography"),
]

# code, kind, building, floor, serviceable
FACILITIES = [
    ("FAC-AIRCON-01", "HVAC", "Main Building", 2, True),
    ("FAC-AIRCON-02", "HVAC", "Main Building", 3, True),
    ("FAC-AIRCON-03", "HVAC", "Science Wing", 1, False),
    ("FAC-PROJ-01", "Projector", "Main Building", 1, True),
    ("FAC-PROJ-02", "Projector", "Main Building", 2, True),
    ("FAC-PROJ-03", "Projector", "Science Wing", 2, True),
    ("FAC-PROJ-04", "Projector", "Annex", 1, False),
    ("FAC-GEN-01", "Generator", "Main Building", 1, True),
    ("FAC-PUMP-01", "Water pump", "Gymnasium", 1, True),
    ("FAC-NET-01", "Network cabinet", "Main Building", 1, True),
    ("FAC-NET-02", "Network cabinet", "Annex", 2, True),
    ("FAC-LAB-01", "Fume hood", "Science Wing", 1, True),
    ("FAC-LAB-02", "Autoclave", "Science Wing", 2, False),
    ("FAC-COURT-01", "Court flooring", "Gymnasium", 1, True),
    ("FAC-PA-01", "PA system", "Gymnasium", 1, True),
    ("FAC-CCTV-01", "CCTV cluster", "Main Building", 1, True),
]

FACILITY_KINDS = sorted({kind for _code, kind, *_rest in FACILITIES})

# title, category, priority
REQUEST_TITLES = [
    ("Aircon not cooling", "Repair", "High"),
    ("Projector bulb replacement", "Repair", "Normal"),
    ("Ceiling leak after the storm", "Repair", "Urgent"),
    ("Deep clean before the open house", "Cleaning", "Normal"),
    ("Weekly laboratory glassware wash", "Cleaning", "Low"),
    ("Stage setup for the recognition rites", "Setup", "High"),
    ("Chairs for the parents' assembly", "Setup", "Normal"),
    ("Wi-Fi drops in the annex", "IT support", "High"),
    ("New network switch for the library", "IT support", "Normal"),
    ("Printer toner for the registrar", "Supplies", "Low"),
    ("Whiteboard markers, first quarter", "Supplies", "Low"),
    ("Replace broken window pane", "Repair", "High"),
    ("Court flooring re-varnish", "Repair", "Normal"),
    ("PA system check before assembly", "IT support", "Normal"),
    ("Fume hood certification", "Repair", "Urgent"),
]

COMPLAINTS = ["Headache", "Fever", "Stomach ache", "Injury", "Allergy", "Other"]

CLINIC_NOTES = [
    "<p>Rested at the clinic for thirty minutes. Vitals normal on release.</p>",
    "<p>Given paracetamol with the guardian's standing consent. Advised to hydrate.</p>",
    "<p>Minor abrasion cleaned and dressed. <strong>No stitches needed.</strong></p>",
    "<p>Adviser informed. Follow-up if the symptoms return tomorrow.</p>",
    "<p>Sent home with the guardian. Endorsed to the family physician.</p>",
]

# concern, status, priority
CASES = [
    ("Repeated tardiness", "Open", "Medium"),
    ("Family situation affecting attendance", "In progress", "High"),
    ("Peer conflict in the annex", "Closed", "Medium"),
    ("Academic support referral", "In progress", "Low"),
    ("Anxiety before the qualifying exam", "In progress", "High"),
    ("Bullying report, under review", "Open", "High"),
    ("Scholarship interview coaching", "Closed", "Low"),
    ("Career guidance, senior high track", "Closed", "Low"),
    ("Extended medical absence follow-up", "Referred", "Medium"),
    ("Adjustment support, transferee", "In progress", "Medium"),
    ("Attendance contract review", "Open", "Medium"),
    ("Exit interview, transferring out", "Closed", "Low"),
]

STUDENT_NOTES = [
    "<p>Transferred in from a public school. Records complete.</p>",
    "<p>Guardian requests <strong>e-mail</strong> for all announcements.</p>",
    "<p>On the varsity roster; excused during tournament weeks.</p>",
    "<p>Applying for the academic scholarship next term.</p>",
    None, None, None,
]


def _weekdays(start: date, end: date) -> list:
    """Every school day in a span — Monday to Friday, which is what an attendance
    register and a term's worth of timestamps both have to fall on."""
    days, cursor = [], start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


class World:
    """What has been built so far, so later phases can reference earlier ones.

    Lists and their columns are held by `name` because that is what the generators
    below read like — `w.col("students", "full_name")` rather than a bare uuid.
    `rows` holds each list's record uuids in insertion order, which is how one list
    links to another.
    """

    def __init__(self, db, report, rng):
        self.db = db
        self.report = report
        self.rng = rng
        self.users = {}
        self.groups = {}
        self.workspaces = {}
        self.lists = {}
        self.columns = {}
        self.rows = {}
        # Generated record dicts kept alongside the uuids, for the phases that need
        # to read back what they wrote (which section belongs to which course).
        self.generated = {}

    def col(self, list_name, column_name):
        return self.columns[list_name][column_name]

    def row(self, list_name, index):
        """One record's uuid, or None if the list came out shorter than generated.

        Only possible when a previous run wrote a different set — the generators are
        deterministic. Answering None keeps a re-run building instead of crashing on
        an index the database no longer has.
        """
        rows = self.rows.get(list_name) or []
        return rows[index] if index < len(rows) else None


async def _build_list(w, workspace, owner, *, name, display_name, description,
                      columns, primary=None):
    """A list, its columns in declared order, and its display field.

    Positions start at 0 and the auto-provisioned audit columns sit at 1000+, so the
    declared order is the order they appear in and the audit trail stays last.
    """
    lst = await ensure_list(
        w.db, w.report, workspace,
        name=name, display_name=display_name, description=description,
    )
    by_name = {}
    for position, spec in enumerate(columns):
        by_name[spec["name"]] = await ensure_column(
            w.db, w.report, lst, owner, position=position, **spec
        )
    w.lists[name] = lst
    w.columns[name] = by_name
    if primary is not None:
        w.lists[name] = await set_primary_column(w.db, lst, by_name[primary])
    return w.lists[name]


def _fields(w, list_name, *names, hidden=(), read_only=()):
    """A form's field layout, in the order given.

    Everything named is included; anything in `hidden` is carried with
    `included: false`, which is how exclusion is spelled — a column merely *absent*
    from a non-empty layout is appended rather than dropped (forms §8 of the
    data-model doc).
    """
    fields = [
        {"column_uuid": str(w.col(list_name, name).uuid), "included": True,
         "enabled": name not in read_only, "order": order}
        for order, name in enumerate(names)
    ]
    fields += [
        {"column_uuid": str(w.col(list_name, name).uuid), "included": False,
         "enabled": False, "order": len(names) + offset}
        for offset, name in enumerate(hidden)
    ]
    return {"fields": fields}


def _filter(w, list_name, *conditions, conjunction="and"):
    """A view's filter group, from (column name, operator, value) triples.

    Values are compared as **text** against the JSONB cell, except on `number`
    columns, which are cast. That is why a checkbox filter below reads `"true"` and
    not Python's `True`: the stored JSON is `true`, and `str(True)` is `"True"`.
    """
    return {
        "conjunction": conjunction,
        "conditions": [
            {"column_uuid": str(w.col(list_name, name).uuid),
             "operator": operator, "value": value}
            for name, operator, value in conditions
        ],
    }


def _sorts(w, list_name, *specs):
    return [
        {"column_uuid": str(w.col(list_name, name).uuid), "direction": direction}
        for name, direction in specs
    ]


def _group_by(w, list_name, column_name):
    return {"column_uuid": str(w.col(list_name, column_name).uuid)}


def _uuids(w, list_name, *names):
    return [str(w.col(list_name, name).uuid) for name in names]


# -- phase 1: principals ---------------------------------------------------


async def build_principals(w, password):
    """Accounts, their personal details, and the groups access is granted through."""
    await ensure_system_groups(w.db)

    admin = await ensure_user(
        w.db, w.report,
        username="admin", email="admin@example.com",
        password=password, is_superuser=True,
    )
    w.users["admin"] = admin
    await ensure_person(
        w.db, w.report, admin,
        name_prefix="Mr", first_name="Arturo", last_name="Delgado",
        birth_date=date(1982, 4, 2),
    )

    for username, prefix, first, middle, last, suffix, born, _role in STAFF:
        user = await ensure_user(
            w.db, w.report,
            username=username,
            email=f"{username}@westbrook.example.edu",
            password=password,
        )
        w.users[username] = user
        await ensure_person(
            w.db, w.report, user,
            name_prefix=prefix, first_name=first, middle_name=middle,
            last_name=last, name_suffix=suffix, birth_date=date(*born),
        )

    for name, (display_name, description, members) in GROUPS.items():
        w.groups[name] = await ensure_group(
            w.db, w.report,
            name=name, display_name=display_name, description=description,
            members=[w.users[username] for username in members],
        )

    # The virtual group every signed-in account belongs to. Seeded by the app at
    # startup; seeded here too, because this script may run before it ever has.
    w.groups["everyone"] = await UserGroupService.get_by_name(
        w.db, AUTHENTICATED_USERS_NAME
    )

    for username, prefs in PREFERENCES.items():
        await set_settings(w.db, w.users[username], **prefs)


# -- phase 2: reference data -----------------------------------------------


async def build_reference(w):
    """The three lists everything else points at: departments, rooms, year levels.

    Shared with `Authenticated Users` as **`lookup` only** — the case that action
    exists for. Every staff account's lookup columns resolve "Sciences" and "MB-201"
    as labels, while the lists themselves stay out of everyone's "Shared with me"
    and cannot be opened.
    """
    registrar = w.users["s.villanueva"]
    workspace = await ensure_workspace(
        w.db, w.report, registrar,
        name="reference", display_name="Reference Data",
        description="Shared code lists the rest of the school records point at.",
    )
    w.workspaces["reference"] = workspace

    await _build_list(
        w, workspace, registrar,
        name="departments", display_name="Departments",
        description="Teaching departments and who heads each one.",
        primary="name",
        columns=[
            {"name": "name", "display_name": "Department", "type": "text",
             "config": {"required": True, "unique": True, "max_length": 80}},
            {"name": "code", "display_name": "Code", "type": "text",
             "config": {"required": True, "unique": True, "pattern": "^[A-Z]{2,5}$",
                        "help_text": "Two to five capital letters, e.g. MATH."}},
            {"name": "head", "display_name": "Department head", "type": "user"},
            {"name": "remit", "display_name": "Remit", "type": "text",
             "config": {"text_mode": "multiline"}},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="rooms", display_name="Rooms",
        description="Every teaching space, its capacity and its home department.",
        primary="code",
        columns=[
            {"name": "code", "display_name": "Room", "type": "text",
             "config": {"required": True, "unique": True}},
            {"name": "building", "display_name": "Building", "type": "choice",
             "config": {"required": True, "options": BUILDINGS}},
            {"name": "floor", "display_name": "Floor", "type": "number",
             "config": {"integer_only": True, "min": 1, "max": 4}},
            {"name": "capacity", "display_name": "Seats", "type": "number",
             "config": {"integer_only": True, "min": 0, "max": 80}},
            {"name": "department", "display_name": "Home department", "type": "lookup",
             "config": {"target_list_uuid": w.lists["departments"].uuid}},
            {"name": "projector", "display_name": "Has projector", "type": "checkbox"},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="grade_levels", display_name="Year Levels",
        description="Junior and senior high year levels.",
        primary="name",
        columns=[
            {"name": "name", "display_name": "Year level", "type": "text",
             "config": {"required": True, "unique": True}},
            {"name": "level", "display_name": "Level", "type": "number",
             "config": {"required": True, "integer_only": True, "min": 7, "max": 12}},
            {"name": "stage", "display_name": "Stage", "type": "choice",
             "config": {"required": True, "options": ["Junior High", "Senior High"]}},
        ],
    )

    # Lookup-only, and before any column points here: a lookup's target is checked
    # against its *author's* access at configuration time, and until this runs only
    # the registrar can read these lists.
    for name in ("departments", "rooms", "grade_levels"):
        await grant(
            w.db, w.report, registrar,
            resource_type=ResourceType.list, resource_uuid=w.lists[name].uuid,
            actions=LOOKUP_ONLY, group=w.groups["everyone"],
        )

    w.rows["departments"] = await seed_records(
        w.db, w.report, w.lists["departments"], registrar, w.columns["departments"],
        [{"name": name, "code": code, "head": head, "remit": remit}
         for name, code, head, remit in DEPARTMENTS],
    )
    w.rows["rooms"] = await seed_records(
        w.db, w.report, w.lists["rooms"], registrar, w.columns["rooms"],
        [{"code": code, "building": building, "floor": floor, "capacity": seats,
          "department": department, "projector": projector}
         for code, building, floor, seats, department, projector in ROOMS],
    )
    w.rows["grade_levels"] = await seed_records(
        w.db, w.report, w.lists["grade_levels"], registrar, w.columns["grade_levels"],
        [{"name": name, "level": level, "stage": stage}
         for name, level, stage in GRADE_LEVELS],
    )

    await ensure_view(
        w.db, w.report, w.lists["rooms"],
        name="by_building", display_name="By building", position=1,
        description="Grouped by building, largest rooms first.",
        config={"group_by": _group_by(w, "rooms", "building"),
                "sorts": _sorts(w, "rooms", ("capacity", "desc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["rooms"],
        name="lecture_rooms", display_name="Projector rooms", position=2,
        description="Rooms that can take a presentation.",
        config={"filters": _filter(w, "rooms", ("projector", "is", "true")),
                "sorts": _sorts(w, "rooms", ("code", "asc"))},
    )
    await ensure_metric(w.db, w.report, w.lists["rooms"],
                        name="room_count", display_name="Rooms", formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["rooms"],
                        name="total_seats", display_name="Total seats",
                        formula="SUM(capacity)", position=1,
                        description="Every seat the school can timetable at once.")
    await ensure_metric(w.db, w.report, w.lists["rooms"],
                        name="average_room", display_name="Average room",
                        formula="AVG(capacity)", position=2, precision=1,
                        suffix=" seats")
    await ensure_metric(w.db, w.report, w.lists["departments"],
                        name="department_count", display_name="Departments",
                        formula="COUNT()")


# -- phase 3: academics ----------------------------------------------------


async def build_academics(w):
    """Students, courses, sections and enrolments — the heart of the scenario.

    Where the two lookup features earn their place: `students.year_level` reaches
    across workspaces into Reference, and `enrollments.section` **nests** under
    `enrollments.course`, so choosing a course narrows the sections on offer and a
    mismatched pair is refused on write rather than merely hidden.
    """
    registrar = w.users["s.villanueva"]
    workspace = await ensure_workspace(
        w.db, w.report, registrar,
        name="academics", display_name="Academics",
        description="The student record: enrolment, subjects, sections and grades.",
    )
    w.workspaces["academics"] = workspace

    await _build_list(
        w, workspace, registrar,
        name="students", display_name="Students",
        description="One row per learner. The registrar owns this list.",
        primary="full_name",
        columns=[
            {"name": "student_no", "display_name": "Student no.", "type": "text",
             "config": {"required": True, "unique": True, "pattern": "^WA-[0-9]{4}$",
                        "placeholder": "WA-0001",
                        "help_text": "Permanent. Assigned at admission."}},
            {"name": "full_name", "display_name": "Name", "type": "text",
             "config": {"required": True, "max_length": 120}},
            {"name": "preferred_name", "display_name": "Goes by", "type": "text"},
            {"name": "birth_date", "display_name": "Date of birth", "type": "date",
             "config": {"min": "2005-01-01", "max": "2016-12-31"}},
            {"name": "year_level", "display_name": "Year level", "type": "lookup",
             "config": {"required": True,
                        "target_list_uuid": w.lists["grade_levels"].uuid}},
            {"name": "programs", "display_name": "Programmes", "type": "choice",
             "config": {"multiple": True,
                        "options": ["STEM", "ABM", "HUMSS", "Sports", "Arts",
                                    "Robotics Club", "Debate", "School Paper"]}},
            {"name": "status", "display_name": "Status", "type": "choice",
             "config": {"required": True, "default": "Enrolled",
                        "options": ["Enrolled", "On leave", "Transferred out",
                                    "Graduated"]}},
            {"name": "gpa", "display_name": "General average", "type": "number",
             "config": {"min": 60, "max": 100, "precision": 2}},
            {"name": "standing", "display_name": "Standing", "type": "calculated",
             "config": {"formula": 'IF(gpa >= 90, "With honours", '
                                   'IF(gpa >= 85, "Good standing", '
                                   'IF(gpa >= 75, "Regular", "At risk")))',
                        "help_text": "Derived from the general average, on read."}},
            {"name": "honour_points", "display_name": "Honour points",
             "type": "calculated",
             "config": {"formula": "IF(gpa >= 90, 1, 0)",
                        "help_text": "1 for an honour student, 0 otherwise — what "
                                     "the 'With honours' metric totals."}},
            {"name": "scholarship", "display_name": "Scholarship", "type": "checkbox"},
            {"name": "adviser", "display_name": "Adviser", "type": "user"},
            {"name": "guardian_email", "display_name": "Guardian e-mail", "type": "link",
             "config": {"allowed_schemes": ["mailto"],
                        "help_text": "Narrowed to mailto: — never a web link."}},
            {"name": "portfolio_url", "display_name": "Portfolio", "type": "link",
             "config": {"allowed_schemes": ["https"]}},
            {"name": "notes", "display_name": "Registrar's notes", "type": "text",
             "config": {"text_mode": "rich"}},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="courses", display_name="Courses",
        description="The subject catalogue for the school year.",
        primary="title",
        columns=[
            {"name": "code", "display_name": "Code", "type": "text",
             "config": {"required": True, "unique": True}},
            {"name": "title", "display_name": "Course", "type": "text",
             "config": {"required": True}},
            {"name": "department", "display_name": "Department", "type": "lookup",
             "config": {"required": True,
                        "target_list_uuid": w.lists["departments"].uuid}},
            {"name": "year_level", "display_name": "Year level", "type": "lookup",
             "config": {"target_list_uuid": w.lists["grade_levels"].uuid}},
            {"name": "term", "display_name": "Term", "type": "choice",
             "config": {"required": True,
                        "options": ["First Semester", "Second Semester", "Full Year"]}},
            {"name": "units", "display_name": "Units", "type": "number",
             "config": {"required": True, "integer_only": True, "min": 1, "max": 5}},
            {"name": "description", "display_name": "Description", "type": "text",
             "config": {"text_mode": "multiline"}},
            {"name": "syllabus_url", "display_name": "Syllabus", "type": "link"},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="sections", display_name="Sections",
        description="A course, a teacher, a room and a timeslot.",
        primary="code",
        columns=[
            {"name": "code", "display_name": "Section", "type": "text",
             "config": {"required": True, "unique": True}},
            # The filter column the nested lookup on `enrollments` matches against: a
            # section belongs to exactly one course, which is what lets choosing a
            # course narrow the sections offered.
            {"name": "course", "display_name": "Course", "type": "lookup",
             "config": {"required": True, "target_list_uuid": w.lists["courses"].uuid}},
            {"name": "teacher", "display_name": "Teacher", "type": "user"},
            {"name": "room", "display_name": "Room", "type": "lookup",
             "config": {"target_list_uuid": w.lists["rooms"].uuid}},
            {"name": "days", "display_name": "Days", "type": "choice",
             "config": {"required": True, "options": ["MWF", "TTh", "Daily", "Sat"]}},
            {"name": "start_time", "display_name": "Starts", "type": "time",
             "config": {"min": "07:00:00", "max": "18:00:00"}},
            {"name": "end_time", "display_name": "Ends", "type": "time",
             "config": {"min": "07:00:00", "max": "19:00:00"}},
            {"name": "capacity", "display_name": "Capacity", "type": "number",
             "config": {"required": True, "integer_only": True, "min": 1, "max": 60}},
            {"name": "enrolled", "display_name": "Enrolled", "type": "number",
             "config": {"integer_only": True, "min": 0, "max": 60}},
            {"name": "open_slots", "display_name": "Open slots", "type": "calculated",
             "config": {"formula": "capacity - enrolled"}},
            {"name": "remarks", "display_name": "Remarks", "type": "text"},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="enrollments", display_name="Enrolments",
        description="One row per learner per section, and the grade it earned.",
        columns=[
            {"name": "student", "display_name": "Student", "type": "lookup",
             "config": {"required": True, "target_list_uuid": w.lists["students"].uuid}},
            {"name": "course", "display_name": "Course", "type": "lookup",
             "config": {"required": True, "target_list_uuid": w.lists["courses"].uuid}},
            # The nesting is paired on afterwards, not here: the parent is a sibling
            # on this same list, so it does not exist yet while this is created.
            {"name": "section", "display_name": "Section", "type": "lookup",
             "config": {"required": True,
                        "target_list_uuid": w.lists["sections"].uuid}},
            {"name": "term", "display_name": "Term", "type": "choice",
             "config": {"required": True,
                        "options": ["First Semester", "Second Semester", "Full Year"]}},
            {"name": "final_grade", "display_name": "Final grade", "type": "number",
             "config": {"min": 0, "max": 100, "precision": 2}},
            {"name": "pass_points", "display_name": "Pass points", "type": "calculated",
             "config": {"formula": "IF(final_grade >= 75, 1, 0)",
                        "help_text": "What the pass-rate metric totals."}},
            {"name": "remark", "display_name": "Remark", "type": "calculated",
             "config": {"formula": 'IF(final_grade >= 75, "Passed", "Failed")'}},
            {"name": "status", "display_name": "Status", "type": "choice",
             "config": {"required": True, "default": "Enrolled",
                        "options": ["Enrolled", "Completed", "Dropped", "Incomplete"]}},
            {"name": "enrolled_at", "display_name": "Enrolled at", "type": "datetime",
             "config": {"default": "now"}},
            {"name": "encoded_by", "display_name": "Encoded by", "type": "user"},
        ],
    )
    await _nest_section_under_course(w)

    await _seed_students(w, registrar)
    await _seed_courses(w, registrar)
    await _seed_sections(w, registrar)
    await _seed_enrollments(w, registrar)
    await _academics_views_and_metrics(w)


async def _nest_section_under_course(w):
    """Pair `enrollments.section` to `enrollments.course`.

    A config PATCH merges onto what is stored, so this sends only the pairing and
    leaves the target list and everything else on the column untouched.
    """
    section = w.col("enrollments", "section")
    if (section.config or {}).get("parent_column_uuid"):
        return
    w.columns["enrollments"]["section"] = await ColumnService.update(
        w.db, w.lists["enrollments"], section.uuid,
        ColumnUpdate(config=ColumnConfig(
            parent_column_uuid=w.col("enrollments", "course").uuid,
            target_filter_column_uuid=w.col("sections", "course").uuid,
        )),
        w.users["s.villanueva"],
    )


async def _seed_students(w, registrar):
    rng = w.rng
    advisers = ["l.tan", "a.mercado", "j.cruz", "p.navarro", "r.ibarra",
                "k.bautista", "e.gutierrez"]
    programs_by_stage = {
        "Junior High": ["Sports", "Arts", "Robotics Club", "Debate", "School Paper"],
        "Senior High": ["STEM", "ABM", "HUMSS", "Robotics Club", "Debate"],
    }

    rows, taken = [], set()
    for index in range(60):
        while True:
            name = f"{rng.choice(GIVEN_NAMES)} {rng.choice(SURNAMES)}"
            if name not in taken:
                taken.add(name)
                break
        level_index = rng.randrange(len(GRADE_LEVELS))
        _level_name, level_number, stage = GRADE_LEVELS[level_index]
        # A learner in year N was born roughly 2026 - (N + 6) years ago.
        birth = date(2026 - (level_number + 6), rng.randint(1, 12), rng.randint(1, 28))
        gpa = round(rng.triangular(72, 98, 87), 2)
        status = rng.choices(
            ["Enrolled", "On leave", "Transferred out"], weights=[92, 5, 3]
        )[0]
        programs = rng.sample(programs_by_stage[stage], rng.randint(0, 2))
        first = name.split()[0]
        rows.append({
            "student_no": f"WA-{index + 1:04d}",
            "full_name": name,
            "preferred_name": first if rng.random() < 0.25 else None,
            "birth_date": birth.isoformat(),
            "year_level": w.row("grade_levels", level_index),
            "programs": programs or None,
            "status": status,
            "gpa": gpa,
            "scholarship": gpa >= 92,
            "adviser": rng.choice(advisers),
            "guardian_email": f"mailto:{first.lower()}.guardian@example.com",
            "portfolio_url": (
                f"https://portfolio.westbrook.example.edu/wa-{index + 1:04d}"
                if rng.random() < 0.3 else None
            ),
            "notes": rng.choice(STUDENT_NOTES),
        })
        # Kept for the enrolment generator, which needs the year level back.
        rows[-1]["_level_index"] = level_index

    w.generated["students"] = rows
    w.rows["students"] = await seed_records(
        w.db, w.report, w.lists["students"], registrar, w.columns["students"],
        [{key: value for key, value in row.items() if not key.startswith("_")}
         for row in rows],
    )


async def _seed_courses(w, registrar):
    rows = [
        {"code": code, "title": title, "department": department,
         "year_level": level, "term": term, "units": units,
         "description": f"{title} for {level}. {units} units, {term.lower()}.",
         "syllabus_url": f"https://westbrook.example.edu/syllabi/{code.lower()}"}
        for code, title, department, level, term, units, _teacher in COURSES
    ]
    w.generated["courses"] = rows
    w.rows["courses"] = await seed_records(
        w.db, w.report, w.lists["courses"], registrar, w.columns["courses"], rows
    )


async def _seed_sections(w, registrar):
    """One or two sections per course, timetabled into fixed slots."""
    rng = w.rng
    slots = [("07:30:00", "08:30:00"), ("08:45:00", "09:45:00"),
             ("10:00:00", "11:00:00"), ("13:00:00", "14:00:00"),
             ("14:15:00", "15:15:00"), ("15:30:00", "16:30:00")]
    room_codes = [room[0] for room in ROOMS]

    rows, slot_index = [], 0
    for course_index, course in enumerate(COURSES):
        code, _title, _dept, _level, _term, _units, teacher = course
        # Alternating, so the demo has both single- and multi-section courses.
        for suffix in (["A", "B"] if course_index % 2 == 0 else ["A"]):
            start, end = slots[slot_index % len(slots)]
            rows.append({
                "code": f"{code}-{suffix}",
                "course": w.row("courses", course_index),
                "teacher": teacher,
                "room": rng.choice(room_codes),
                "days": rng.choice(["MWF", "TTh", "Daily"]),
                "start_time": start,
                "end_time": end,
                "capacity": rng.choice([30, 32, 35, 38, 40]),
                "enrolled": 0,
                # Written on purpose: `remarks` is the column phase 8 bins, and a
                # binned column's values stay in `data` — hidden from every reader
                # until it is restored. An empty column would demonstrate nothing.
                "remarks": rng.choice([
                    None, None,
                    "Shares the laboratory with the afternoon section.",
                    "Timetabled around the varsity training block.",
                    "Room changes to the annex during the fair.",
                ]),
            })
            rows[-1]["_course_index"] = course_index
            slot_index += 1

    w.generated["sections"] = rows
    w.rows["sections"] = await seed_records(
        w.db, w.report, w.lists["sections"], registrar, w.columns["sections"],
        [{key: value for key, value in row.items() if not key.startswith("_")}
         for row in rows],
    )


async def _seed_enrollments(w, registrar):
    """Three to five subjects per active learner, always at their own year level.

    `course` is taken from the chosen section rather than picked independently: the
    nested pairing is enforced on write, so an inconsistent pair would be a 400
    rather than a slightly odd row.
    """
    rng = w.rng

    # Which sections a given year level can be enrolled in.
    by_level = {}
    for position, section in enumerate(w.generated["sections"]):
        course = COURSES[section["_course_index"]]
        by_level.setdefault(course[3], []).append(
            (position, w.row("sections", position),
             w.row("courses", section["_course_index"]), course[4])
        )

    encoders = ["s.villanueva", "m.reyes"]
    tally, rows = {}, []
    for index, student in enumerate(w.generated["students"]):
        if student["status"] == "Transferred out":
            continue
        level_name = GRADE_LEVELS[student["_level_index"]][0]
        available = by_level.get(level_name, [])
        if not available:
            continue
        chosen = rng.sample(available, min(len(available), rng.randint(3, 5)))
        for position, section_uuid, course_uuid, term in chosen:
            if section_uuid is None or course_uuid is None:
                continue
            grade = round(min(100, max(60, rng.gauss(student["gpa"], 5))), 2)
            completed = rng.random() < 0.8
            enrolled_at = datetime(2026, 6, 1, 8, 0) + timedelta(
                days=rng.randint(0, 10), minutes=rng.randint(0, 480)
            )
            rows.append({
                "student": w.row("students", index),
                "course": course_uuid,
                "section": section_uuid,
                "term": term,
                "final_grade": grade if completed else None,
                "status": "Completed" if completed else rng.choice(
                    ["Enrolled", "Incomplete", "Dropped"]
                ),
                "enrolled_at": enrolled_at.isoformat(timespec="seconds"),
                "encoded_by": rng.choice(encoders),
            })
            tally[position] = tally.get(position, 0) + 1

    rows = [row for row in rows if row["student"] is not None]
    w.rows["enrollments"] = await seed_records(
        w.db, w.report, w.lists["enrollments"], registrar,
        w.columns["enrollments"], rows,
    )
    await _backfill_section_counts(w, registrar, tally)


async def _backfill_section_counts(w, registrar, tally):
    """Write each section's headcount onto it, so `open_slots` means something.

    Skipped where the stored count already matches. Writing it regardless would be
    harmless to the number but not to the record: `updated_at` and `updated_by` are
    re-stamped on every write, so a re-run would show twenty sections edited by the
    registrar seconds ago and nothing actually changed.
    """
    key = str(w.col("sections", "enrolled").uuid)
    for position, count in tally.items():
        uuid = w.row("sections", position)
        if uuid is None:
            continue
        current = await ListItemService.get_by_uuid(w.db, w.lists["sections"].id, uuid)
        if current is not None and (current.data or {}).get(key) == count:
            continue
        await ListItemService.update(
            w.db, w.lists["sections"], uuid,
            ListItemUpdate(data={key: count}), registrar,
        )
        w.report["records updated"] += 1


async def _academics_views_and_metrics(w):
    students, enrollments = w.lists["students"], w.lists["enrollments"]

    await ensure_view(
        w.db, w.report, students,
        name="enrolled", display_name="Currently enrolled", position=1,
        description="Active learners only, by name.",
        config={"filters": _filter(w, "students", ("status", "is", "Enrolled")),
                "sorts": _sorts(w, "students", ("full_name", "asc")),
                "hidden_columns": _uuids(w, "students", "notes", "portfolio_url"),
                "page_size": 25},
    )
    await ensure_view(
        w.db, w.report, students,
        name="by_year_level", display_name="By year level", position=2,
        description="Grouped by year level — the register the advisers read.",
        config={"group_by": _group_by(w, "students", "year_level"),
                "sorts": _sorts(w, "students", ("full_name", "asc")),
                "column_order": _uuids(w, "students", "full_name", "student_no",
                                       "year_level", "adviser", "gpa", "standing")},
    )
    await ensure_view(
        w.db, w.report, students,
        name="honour_roll", display_name="Honour roll", position=3,
        description="General average of 90 and above.",
        config={"filters": _filter(w, "students", ("gpa", ">=", 90)),
                "sorts": _sorts(w, "students", ("gpa", "desc")),
                "hidden_columns": _uuids(w, "students", "notes", "guardian_email")},
    )
    await ensure_view(
        w.db, w.report, students,
        name="scholars", display_name="Scholarship holders", position=4,
        config={"filters": _filter(w, "students", ("scholarship", "is", "true")),
                "sorts": _sorts(w, "students", ("full_name", "asc"))},
    )
    await ensure_form(
        w.db, w.report, students,
        name="admissions_intake", display_name="Admissions intake",
        type=FormType.create,
        config=_fields(
            w, "students",
            "student_no", "full_name", "preferred_name", "birth_date", "year_level",
            "guardian_email",
            hidden=("gpa", "standing", "honour_points", "notes", "scholarship"),
        ),
    )
    await ensure_form(
        w.db, w.report, students,
        name="adviser_card", display_name="Adviser's card", type=FormType.view,
        config=_fields(
            w, "students",
            "full_name", "student_no", "year_level", "adviser", "programs",
            "gpa", "standing", "notes",
        ),
    )
    for position, (name, display_name, formula, kwargs) in enumerate([
        ("student_count", "Students", "COUNT()", {}),
        ("average_gpa", "Average general average", "AVG(gpa)", {"precision": 2}),
        ("honour_students", "With honours", "SUM(honour_points)", {}),
        ("top_average", "Highest average", "MAX(gpa)", {"precision": 2}),
    ]):
        await ensure_metric(w.db, w.report, students, name=name,
                            display_name=display_name, formula=formula,
                            position=position, **kwargs)

    await ensure_view(
        w.db, w.report, w.lists["courses"],
        name="by_department", display_name="By department", position=1,
        config={"group_by": _group_by(w, "courses", "department"),
                "sorts": _sorts(w, "courses", ("code", "asc"))},
    )
    await ensure_metric(w.db, w.report, w.lists["courses"],
                        name="course_count", display_name="Courses", formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["courses"],
                        name="total_units", display_name="Total units",
                        formula="SUM(units)", position=1)

    await ensure_view(
        w.db, w.report, w.lists["sections"],
        name="morning", display_name="Morning classes", position=1,
        description="Anything starting before lunch.",
        config={"filters": _filter(w, "sections", ("start_time", "<", "12:00:00")),
                "sorts": _sorts(w, "sections", ("start_time", "asc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["sections"],
        name="by_course", display_name="By course", position=2,
        config={"group_by": _group_by(w, "sections", "course"),
                "sorts": _sorts(w, "sections", ("code", "asc"))},
    )
    await ensure_metric(w.db, w.report, w.lists["sections"],
                        name="section_count", display_name="Sections", formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["sections"],
                        name="seats_taken", display_name="Seats taken",
                        formula="SUM(enrolled)", position=1)
    await ensure_metric(w.db, w.report, w.lists["sections"],
                        name="seats_open", display_name="Seats still open",
                        formula="SUM(open_slots)", position=2,
                        description="Totals a calculated column: capacity minus enrolled.")

    await ensure_view(
        w.db, w.report, enrollments,
        name="failing", display_name="Below the passing mark", position=1,
        description="Final grade under 75, worst first.",
        config={"filters": _filter(w, "enrollments", ("final_grade", "<", 75)),
                "sorts": _sorts(w, "enrollments", ("final_grade", "asc")),
                "page_size": 100},
    )
    await ensure_view(
        w.db, w.report, enrollments,
        name="by_status", display_name="By status", position=2,
        config={"group_by": _group_by(w, "enrollments", "status"), "page_size": 100},
    )
    await ensure_view(
        w.db, w.report, enrollments,
        name="needs_a_grade", display_name="Needs a grade", position=3,
        description="No final grade encoded yet, oldest enrolment first.",
        config={"filters": _filter(w, "enrollments", ("final_grade", "is_empty", None)),
                "sorts": _sorts(w, "enrollments", ("enrolled_at", "asc"))},
    )
    await ensure_form(
        w.db, w.report, enrollments,
        name="quick_enrol", display_name="Quick enrol", type=FormType.create,
        config=_fields(w, "enrollments", "student", "course", "section", "term",
                       hidden=("final_grade", "encoded_by")),
    )
    for position, (name, display_name, formula, kwargs) in enumerate([
        ("enrolment_count", "Enrolments", "COUNT()", {}),
        ("average_grade", "Average grade", "AVG(final_grade)", {"precision": 2}),
        ("passed", "Passed", "SUM(pass_points)", {}),
        ("pass_rate", "Pass rate",
         "ROUND(100 * SUM(pass_points) / COUNT(final_grade), 1)",
         {"suffix": "%", "precision": 1}),
        ("lowest_grade", "Lowest grade", "MIN(final_grade)", {"precision": 2}),
    ]):
        await ensure_metric(w.db, w.report, enrollments, name=name,
                            display_name=display_name, formula=formula,
                            position=position, **kwargs)


# -- phase 4: student services ---------------------------------------------


async def build_student_services(w):
    """Attendance, the clinic and guidance casework.

    `counseling_cases` is the list that stops inheriting its workspace's grants:
    casework is not for everyone the workspace reaches, so it stands alone with
    grants of its own.
    """
    registrar = w.users["s.villanueva"]
    workspace = await ensure_workspace(
        w.db, w.report, registrar,
        name="student_services", display_name="Student Services",
        description="Attendance, the clinic and guidance casework.",
    )
    w.workspaces["student_services"] = workspace

    await _build_list(
        w, workspace, registrar,
        name="attendance", display_name="Attendance",
        description="The daily register: one row per learner per school day.",
        columns=[
            {"name": "student", "display_name": "Student", "type": "lookup",
             "config": {"required": True, "target_list_uuid": w.lists["students"].uuid}},
            {"name": "school_day", "display_name": "School day", "type": "date",
             "config": {"required": True, "default": "today"}},
            {"name": "status", "display_name": "Status", "type": "choice",
             "config": {"required": True, "default": "Present",
                        "options": ["Present", "Late", "Excused absence",
                                    "Unexcused absence"]}},
            {"name": "minutes_late", "display_name": "Minutes late", "type": "number",
             "config": {"integer_only": True, "min": 0, "max": 240}},
            {"name": "remarks", "display_name": "Remarks", "type": "text"},
            {"name": "recorded_by", "display_name": "Recorded by", "type": "user",
             "config": {"default": "current_user"}},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="clinic_visits", display_name="Clinic Visits",
        description="What the school nurse saw, and what was done about it.",
        columns=[
            {"name": "student", "display_name": "Student", "type": "lookup",
             "config": {"required": True, "target_list_uuid": w.lists["students"].uuid}},
            {"name": "visited_at", "display_name": "Seen at", "type": "datetime",
             "config": {"required": True, "default": "now"}},
            {"name": "complaint", "display_name": "Complaint", "type": "choice",
             "config": {"required": True, "options": COMPLAINTS}},
            {"name": "severity", "display_name": "Severity", "type": "choice",
             "config": {"required": True, "default": "Minor",
                        "options": ["Minor", "Moderate", "Urgent"]}},
            {"name": "temperature", "display_name": "Temperature", "type": "number",
             "config": {"min": 34, "max": 42, "precision": 1,
                        "help_text": "Degrees Celsius."}},
            {"name": "notes", "display_name": "Clinic notes", "type": "text",
             "config": {"text_mode": "rich"}},
            {"name": "seen_by", "display_name": "Seen by", "type": "user"},
            {"name": "sent_home", "display_name": "Sent home", "type": "checkbox"},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="counseling_cases", display_name="Guidance Cases",
        description="Casework. Permissions on this list stand alone — it does not "
                    "inherit the workspace's.",
        primary="reference",
        columns=[
            {"name": "reference", "display_name": "Reference", "type": "text",
             "config": {"required": True, "unique": True,
                        "pattern": "^GC-2026-[0-9]{3}$"}},
            {"name": "title", "display_name": "Concern", "type": "text",
             "config": {"required": True}},
            {"name": "student", "display_name": "Student", "type": "lookup",
             "config": {"target_list_uuid": w.lists["students"].uuid}},
            {"name": "handled_by", "display_name": "Handled by", "type": "group",
             "config": {"help_text": "The team accountable, not one person."}},
            {"name": "counselor", "display_name": "Counsellor", "type": "user"},
            {"name": "status", "display_name": "Status", "type": "choice",
             "config": {"required": True, "default": "Open",
                        "options": ["Open", "In progress", "Referred", "Closed"]}},
            {"name": "priority", "display_name": "Priority", "type": "choice",
             "config": {"required": True, "default": "Medium",
                        "options": ["Low", "Medium", "High"]}},
            {"name": "opened_on", "display_name": "Opened", "type": "date",
             "config": {"required": True, "default": "today"}},
            {"name": "closed_on", "display_name": "Closed", "type": "date"},
            {"name": "summary", "display_name": "Summary", "type": "text",
             "config": {"text_mode": "rich"}},
            {"name": "case_file_url", "display_name": "Case file", "type": "link"},
        ],
    )
    w.lists["counseling_cases"] = await set_unique_permissions(
        w.db, w.lists["counseling_cases"]
    )

    await _seed_student_services(w)
    await _student_services_views_and_metrics(w)


async def _seed_student_services(w):
    rng = w.rng
    days = _weekdays(YEAR_START, TODAY - timedelta(days=1))
    students = [uuid for uuid in (w.rows.get("students") or []) if uuid]

    attendance = []
    for _ in range(180):
        status = rng.choices(
            ["Present", "Late", "Excused absence", "Unexcused absence"],
            weights=[62, 20, 10, 8],
        )[0]
        attendance.append({
            "student": rng.choice(students) if students else None,
            "school_day": rng.choice(days).isoformat(),
            "status": status,
            "minutes_late": rng.randint(5, 45) if status == "Late" else None,
            "remarks": ("Guardian called ahead."
                        if status == "Excused absence" and rng.random() < 0.5 else None),
            "recorded_by": rng.choice(["d.okafor", "s.villanueva"]),
        })
    await seed_records(
        w.db, w.report, w.lists["attendance"], w.users["d.okafor"],
        w.columns["attendance"], attendance if students else [],
    )

    visits = []
    for _ in range(30):
        severity = rng.choices(["Minor", "Moderate", "Urgent"], weights=[70, 25, 5])[0]
        day = rng.choice(days)
        visits.append({
            "student": rng.choice(students) if students else None,
            "visited_at": datetime(
                day.year, day.month, day.day,
                rng.randint(8, 15), rng.choice([0, 15, 30, 45]),
            ).isoformat(timespec="seconds"),
            "complaint": rng.choice(COMPLAINTS),
            "severity": severity,
            "temperature": round(rng.uniform(36.2, 39.4), 1),
            "notes": rng.choice(CLINIC_NOTES),
            "seen_by": "n.santos",
            "sent_home": severity != "Minor" and rng.random() < 0.5,
        })
    await seed_records(
        w.db, w.report, w.lists["clinic_visits"], w.users["n.santos"],
        w.columns["clinic_visits"], visits if students else [],
    )

    cases = []
    for index, (title, status, priority) in enumerate(CASES):
        opened = rng.choice(days[: len(days) // 2])
        closed = opened + timedelta(days=rng.randint(5, 30))
        cases.append({
            "reference": f"GC-2026-{index + 1:03d}",
            "title": title,
            "student": rng.choice(students) if students else None,
            "handled_by": "Student Support",
            "counselor": "d.okafor",
            "status": status,
            "priority": priority,
            "opened_on": opened.isoformat(),
            "closed_on": (closed.isoformat()
                          if status == "Closed" and closed <= TODAY else None),
            "summary": "<p>Initial session held. Adviser and guardian informed; "
                       "follow-up scheduled.</p>",
            "case_file_url": ("https://guidance.westbrook.example.edu/cases/"
                              f"gc-2026-{index + 1:03d}"),
        })
    w.rows["counseling_cases"] = await seed_records(
        w.db, w.report, w.lists["counseling_cases"], w.users["d.okafor"],
        w.columns["counseling_cases"], cases,
    )


async def _student_services_views_and_metrics(w):
    await ensure_view(
        w.db, w.report, w.lists["attendance"],
        name="unexcused", display_name="Unexcused absences", position=1,
        config={"filters": _filter(w, "attendance",
                                   ("status", "is", "Unexcused absence")),
                "sorts": _sorts(w, "attendance", ("school_day", "desc")),
                "page_size": 100},
    )
    await ensure_view(
        w.db, w.report, w.lists["attendance"],
        name="by_status", display_name="By status", position=2,
        config={"group_by": _group_by(w, "attendance", "status"),
                "sorts": _sorts(w, "attendance", ("school_day", "desc")),
                "page_size": 100},
    )
    await ensure_metric(w.db, w.report, w.lists["attendance"],
                        name="entries", display_name="Register entries",
                        formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["attendance"],
                        name="minutes_lost", display_name="Minutes lost to lateness",
                        formula="SUM(minutes_late)", position=1)
    await ensure_metric(
        w.db, w.report, w.lists["attendance"],
        name="average_lateness", display_name="Average, when late",
        formula="AVG(minutes_late)", position=2, precision=1, suffix=" min",
        description="Blank cells are skipped, so this averages the late arrivals only.",
    )

    await ensure_view(
        w.db, w.report, w.lists["clinic_visits"],
        name="urgent", display_name="Urgent", position=1,
        config={"filters": _filter(w, "clinic_visits", ("severity", "is", "Urgent")),
                "sorts": _sorts(w, "clinic_visits", ("visited_at", "desc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["clinic_visits"],
        name="by_complaint", display_name="By complaint", position=2,
        config={"group_by": _group_by(w, "clinic_visits", "complaint")},
    )
    await ensure_metric(w.db, w.report, w.lists["clinic_visits"],
                        name="visit_count", display_name="Visits", formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["clinic_visits"],
                        name="average_temperature", display_name="Average temperature",
                        formula="AVG(temperature)", position=1, precision=1,
                        suffix=" °C")

    await ensure_view(
        w.db, w.report, w.lists["counseling_cases"],
        name="open_cases", display_name="Open cases", position=1,
        description="Anything not yet closed, oldest first.",
        config={"filters": _filter(w, "counseling_cases",
                                   ("status", "is_not", "Closed")),
                "sorts": _sorts(w, "counseling_cases", ("opened_on", "asc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["counseling_cases"],
        name="by_priority", display_name="By priority", position=2,
        config={"group_by": _group_by(w, "counseling_cases", "priority")},
    )
    await ensure_metric(w.db, w.report, w.lists["counseling_cases"],
                        name="case_count", display_name="Cases", formula="COUNT()")


# -- phase 5: the library --------------------------------------------------


async def build_library(w):
    """The catalogue and circulation.

    Owned by the registrar so `loans.borrower` can point at `students` and actually
    be written; run day to day by the librarian, who gets `manage` on the workspace
    in phase 7.
    """
    registrar, librarian = w.users["s.villanueva"], w.users["f.aquino"]
    rng = w.rng
    workspace = await ensure_workspace(
        w.db, w.report, registrar,
        name="library", display_name="Library",
        description="The catalogue and circulation desk.",
    )
    w.workspaces["library"] = workspace

    await _build_list(
        w, workspace, registrar,
        name="catalog", display_name="Catalogue",
        description="Every title the library holds.",
        primary="title",
        columns=[
            {"name": "title", "display_name": "Title", "type": "text",
             "config": {"required": True, "max_length": 200}},
            {"name": "author", "display_name": "Author", "type": "text"},
            {"name": "isbn", "display_name": "ISBN", "type": "text",
             "config": {"unique": True, "pattern": "^[0-9]{13}$",
                        "help_text": "13 digits, no dashes."}},
            {"name": "genre", "display_name": "Genre", "type": "choice",
             "config": {"required": True,
                        "options": ["Fiction", "Filipiniana", "Science", "History",
                                    "Biography", "Reference"]}},
            {"name": "copies", "display_name": "Copies", "type": "number",
             "config": {"required": True, "integer_only": True, "min": 0, "max": 50}},
            {"name": "shelf", "display_name": "Shelf", "type": "text"},
            {"name": "reference_only", "display_name": "Reference only",
             "type": "checkbox",
             "config": {"help_text": "Cannot leave the reading room."}},
            {"name": "catalog_url", "display_name": "Catalogue entry", "type": "link"},
            {"name": "added_on", "display_name": "Accessioned", "type": "date",
             "config": {"default": "today"}},
        ],
    )
    await _build_list(
        w, workspace, registrar,
        name="loans", display_name="Loans",
        description="Who has what, and what it costs when it is late.",
        columns=[
            {"name": "book", "display_name": "Title", "type": "lookup",
             "config": {"required": True, "target_list_uuid": w.lists["catalog"].uuid}},
            {"name": "borrower", "display_name": "Borrower", "type": "lookup",
             "config": {"required": True, "target_list_uuid": w.lists["students"].uuid}},
            {"name": "borrowed_on", "display_name": "Borrowed", "type": "date",
             "config": {"required": True, "default": "today"}},
            {"name": "due_on", "display_name": "Due", "type": "date",
             "config": {"required": True}},
            {"name": "returned_on", "display_name": "Returned", "type": "date"},
            {"name": "status", "display_name": "Status", "type": "choice",
             "config": {"required": True, "default": "On loan",
                        "options": ["On loan", "Returned", "Overdue", "Lost"]}},
            {"name": "fine", "display_name": "Fine", "type": "number",
             "config": {"min": 0, "precision": 2, "number_format": "currency"}},
            {"name": "issued_by", "display_name": "Issued by", "type": "user"},
        ],
    )

    shelves = ["A-1", "A-2", "B-1", "B-2", "C-1", "C-2", "D-1"]
    catalog_rows = []
    for index, (title, author, genre) in enumerate(BOOKS):
        catalog_rows.append({
            "title": title,
            "author": author,
            "isbn": f"978{index + 1:010d}",
            "genre": genre,
            "copies": rng.randint(1, 8),
            "shelf": "REF-1" if genre == "Reference" else rng.choice(shelves),
            "reference_only": genre == "Reference",
            "catalog_url": f"https://library.westbrook.example.edu/title/{index + 1}",
            "added_on": (YEAR_START - timedelta(days=rng.randint(30, 900))).isoformat(),
        })
    w.rows["catalog"] = await seed_records(
        w.db, w.report, w.lists["catalog"], librarian, w.columns["catalog"],
        catalog_rows,
    )

    lendable = [
        uuid for uuid, book in zip(w.rows.get("catalog") or [], catalog_rows)
        if uuid and not book["reference_only"]
    ]
    students = [uuid for uuid in (w.rows.get("students") or []) if uuid]
    loan_rows = []
    for _ in range(60):
        borrowed = TODAY - timedelta(days=rng.randint(1, 55))
        due = borrowed + timedelta(days=14)
        if rng.random() < 0.62:
            returned = borrowed + timedelta(days=rng.randint(2, 20))
            status = "Returned"
            fine = round(max(0, (returned - due).days) * 5.0, 2)
        elif due < TODAY:
            returned, status = None, "Overdue"
            fine = round((TODAY - due).days * 5.0, 2)
        else:
            returned, status, fine = None, "On loan", None
        loan_rows.append({
            "book": rng.choice(lendable) if lendable else None,
            "borrower": rng.choice(students) if students else None,
            "borrowed_on": borrowed.isoformat(),
            "due_on": due.isoformat(),
            "returned_on": returned.isoformat() if returned else None,
            "status": status,
            "fine": fine,
            "issued_by": "f.aquino",
        })
    await seed_records(
        w.db, w.report, w.lists["loans"], librarian, w.columns["loans"],
        loan_rows if (lendable and students) else [],
    )

    await ensure_view(
        w.db, w.report, w.lists["catalog"],
        name="by_genre", display_name="By genre", position=1,
        config={"group_by": _group_by(w, "catalog", "genre"),
                "sorts": _sorts(w, "catalog", ("title", "asc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["catalog"],
        name="reading_room", display_name="Reading room only", position=2,
        config={"filters": _filter(w, "catalog", ("reference_only", "is", "true")),
                "sorts": _sorts(w, "catalog", ("shelf", "asc"))},
    )
    await ensure_metric(w.db, w.report, w.lists["catalog"],
                        name="titles", display_name="Titles", formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["catalog"],
                        name="copies_held", display_name="Copies held",
                        formula="SUM(copies)", position=1)
    await ensure_metric(w.db, w.report, w.lists["catalog"],
                        name="authors", display_name="Distinct authors",
                        formula="UNIQUE(author)", position=2)

    await ensure_view(
        w.db, w.report, w.lists["loans"],
        name="overdue", display_name="Overdue", position=1,
        description="Out past the due date and still not back.",
        config={"filters": _filter(w, "loans", ("status", "is", "Overdue")),
                "sorts": _sorts(w, "loans", ("due_on", "asc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["loans"],
        name="on_loan", display_name="On loan", position=2,
        config={"filters": _filter(w, "loans", ("status", "is", "On loan")),
                "sorts": _sorts(w, "loans", ("due_on", "asc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["loans"],
        name="by_status", display_name="By status", position=3,
        config={"group_by": _group_by(w, "loans", "status"), "page_size": 100},
    )
    await ensure_metric(w.db, w.report, w.lists["loans"],
                        name="loan_count", display_name="Loans", formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["loans"],
                        name="fines_due", display_name="Fines", formula="SUM(fine)",
                        position=1, precision=2, prefix="₱")
    await ensure_metric(w.db, w.report, w.lists["loans"],
                        name="largest_fine", display_name="Largest fine",
                        formula="MAX(fine)", position=2, precision=2, prefix="₱")


# -- phase 6: operations ---------------------------------------------------


async def build_operations(w):
    """The IT admin's estate — the one workspace with a different owner.

    Its lookups stay inside it: `facility_requests.location` points at `facilities`,
    the maintenance team's own asset register, not at the registrar's `rooms`. See
    the module docstring for why that is a rule and not a preference.
    """
    itadmin = w.users["admin"]
    rng = w.rng
    workspace = await ensure_workspace(
        w.db, w.report, itadmin,
        name="operations", display_name="Operations",
        description="Facilities, assets and the staff directory.",
    )
    w.workspaces["operations"] = workspace

    await _build_list(
        w, workspace, itadmin,
        name="facilities", display_name="Facilities",
        description="The physical asset register the maintenance team keeps.",
        primary="code",
        columns=[
            {"name": "code", "display_name": "Asset", "type": "text",
             "config": {"required": True, "unique": True}},
            {"name": "kind", "display_name": "Kind", "type": "choice",
             "config": {"required": True, "options": FACILITY_KINDS}},
            {"name": "building", "display_name": "Building", "type": "choice",
             "config": {"required": True, "options": BUILDINGS}},
            {"name": "floor", "display_name": "Floor", "type": "number",
             "config": {"integer_only": True, "min": 1, "max": 4}},
            {"name": "serviceable", "display_name": "Serviceable", "type": "checkbox"},
            {"name": "last_serviced", "display_name": "Last serviced", "type": "date"},
        ],
    )
    await _build_list(
        w, workspace, itadmin,
        name="staff_directory", display_name="Staff Directory",
        description="Employment details and teaching load.",
        primary="full_name",
        columns=[
            {"name": "full_name", "display_name": "Name", "type": "text",
             "config": {"required": True}},
            {"name": "account", "display_name": "Account", "type": "user",
             "config": {"required": True,
                        "help_text": "The sign-in this row belongs to."}},
            # A choice rather than a lookup at reference/departments, which has a
            # different owner. That used to be forced — a cross-owner lookup took
            # the column and then refused every value — and no longer is; it stays
            # a choice because nothing here needs it to change. See the module
            # docstring.
            {"name": "department", "display_name": "Department", "type": "choice",
             "config": {"options": [name for name, *_ in DEPARTMENTS]
                                   + ["Administration"]}},
            {"name": "position", "display_name": "Position", "type": "text"},
            {"name": "employment_type", "display_name": "Employment", "type": "choice",
             "config": {"required": True, "default": "Full-time",
                        "options": ["Full-time", "Part-time", "Contract",
                                    "Substitute"]}},
            {"name": "primary_group", "display_name": "Primary group", "type": "group"},
            {"name": "hired_on", "display_name": "Hired", "type": "date"},
            {"name": "work_email", "display_name": "Work e-mail", "type": "link",
             "config": {"allowed_schemes": ["mailto"]}},
            {"name": "load_hours", "display_name": "Weekly load", "type": "number",
             "config": {"integer_only": True, "min": 0, "max": 40}},
            {"name": "active", "display_name": "Active", "type": "checkbox"},
        ],
    )
    await _build_list(
        w, workspace, itadmin,
        name="facility_requests", display_name="Facility Requests",
        description="Work the maintenance and IT teams are asked for.",
        primary="title",
        columns=[
            {"name": "title", "display_name": "Request", "type": "text",
             "config": {"required": True}},
            {"name": "location", "display_name": "Asset", "type": "lookup",
             "config": {"target_list_uuid": w.lists["facilities"].uuid}},
            {"name": "requested_by", "display_name": "Requested by", "type": "user",
             "config": {"default": "current_user"}},
            {"name": "assigned_group", "display_name": "Assigned to", "type": "group"},
            {"name": "category", "display_name": "Category", "type": "choice",
             "config": {"required": True,
                        "options": ["Repair", "Cleaning", "Setup", "IT support",
                                    "Supplies"]}},
            {"name": "priority", "display_name": "Priority", "type": "choice",
             "config": {"required": True, "default": "Normal",
                        "options": ["Low", "Normal", "High", "Urgent"]}},
            {"name": "status", "display_name": "Status", "type": "choice",
             "config": {"required": True, "default": "New",
                        "options": ["New", "Scheduled", "In progress", "Done",
                                    "Cancelled"]}},
            {"name": "requested_at", "display_name": "Requested at", "type": "datetime",
             "config": {"default": "now"}},
            {"name": "needed_by", "display_name": "Needed by", "type": "date"},
            {"name": "estimated_cost", "display_name": "Estimated cost",
             "type": "number",
             "config": {"min": 0, "precision": 2, "number_format": "currency"}},
            {"name": "details", "display_name": "Details", "type": "text",
             "config": {"text_mode": "multiline"}},
        ],
    )

    w.rows["facilities"] = await seed_records(
        w.db, w.report, w.lists["facilities"], itadmin, w.columns["facilities"],
        [{"code": code, "kind": kind, "building": building, "floor": floor,
          "serviceable": ok,
          "last_serviced": (TODAY - timedelta(days=rng.randint(20, 400))).isoformat()}
         for code, kind, building, floor, ok in FACILITIES],
    )

    directory = []
    for username in ["admin"] + [person[0] for person in STAFF]:
        person = next((p for p in STAFF if p[0] == username), None)
        if person is None:
            full_name, role, teaching = "Arturo Delgado", "IT administrator", False
        else:
            full_name = " ".join(part for part in (person[2], person[4]) if part)
            role, teaching = person[7], username in DEPARTMENT_OF
        directory.append({
            "full_name": full_name,
            "account": username,
            "department": DEPARTMENT_OF.get(username, "Administration"),
            "position": role,
            "employment_type": "Substitute" if username == "t.reyes" else "Full-time",
            "primary_group": GROUP_OF.get(username, "teachers"),
            "hired_on": date(rng.randint(2012, 2025), rng.randint(1, 12),
                             rng.randint(1, 28)).isoformat(),
            "work_email": f"mailto:{username}@westbrook.example.edu",
            "load_hours": rng.randint(12, 30) if teaching else 0,
            "active": True,
        })
    await seed_records(
        w.db, w.report, w.lists["staff_directory"], itadmin,
        w.columns["staff_directory"], directory,
    )

    assets = [uuid for uuid in (w.rows.get("facilities") or []) if uuid]
    requesters = ["m.reyes", "b.lim", "f.aquino", "l.tan", "c.delacruz", "r.ibarra"]
    assignees = ["Administration", "Library Staff", "Student Support"]
    requests = []
    for index in range(35):
        title, category, priority = REQUEST_TITLES[index % len(REQUEST_TITLES)]
        requested = TODAY - timedelta(days=rng.randint(0, 50))
        requests.append({
            "title": title,
            "location": rng.choice(assets) if assets else None,
            "requested_by": rng.choice(requesters),
            "assigned_group": rng.choice(assignees),
            "category": category,
            "priority": priority,
            "status": rng.choices(
                ["New", "Scheduled", "In progress", "Done", "Cancelled"],
                weights=[18, 20, 22, 35, 5],
            )[0],
            "requested_at": datetime(
                requested.year, requested.month, requested.day,
                rng.randint(7, 17), rng.choice([0, 20, 40]),
            ).isoformat(timespec="seconds"),
            "needed_by": (requested + timedelta(days=rng.randint(3, 21))).isoformat(),
            "estimated_cost": round(rng.uniform(350, 48000), 2),
            "details": "Reported by the department. Photographs are in the shared "
                       "folder.",
        })
    w.rows["facility_requests"] = await seed_records(
        w.db, w.report, w.lists["facility_requests"], itadmin,
        w.columns["facility_requests"], requests,
    )

    await ensure_view(
        w.db, w.report, w.lists["facility_requests"],
        name="open_requests", display_name="Still open", position=1,
        description="Neither finished nor cancelled, oldest first.",
        config={"filters": _filter(w, "facility_requests",
                                   ("status", "is_not", "Done"),
                                   ("status", "is_not", "Cancelled")),
                "sorts": _sorts(w, "facility_requests", ("requested_at", "asc")),
                "page_size": 25},
    )
    await ensure_view(
        w.db, w.report, w.lists["facility_requests"],
        name="urgent", display_name="Urgent", position=2,
        config={"filters": _filter(w, "facility_requests",
                                   ("priority", "is", "Urgent")),
                "sorts": _sorts(w, "facility_requests", ("needed_by", "asc"))},
    )
    await ensure_view(
        w.db, w.report, w.lists["facility_requests"],
        name="by_status", display_name="By status", position=3,
        config={"group_by": _group_by(w, "facility_requests", "status")},
    )
    await ensure_form(
        w.db, w.report, w.lists["facility_requests"],
        name="report_a_problem", display_name="Report a problem",
        type=FormType.create,
        config=_fields(w, "facility_requests",
                       "title", "location", "category", "priority", "needed_by",
                       "details",
                       hidden=("status", "estimated_cost", "assigned_group")),
    )
    await ensure_metric(w.db, w.report, w.lists["facility_requests"],
                        name="request_count", display_name="Requests",
                        formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["facility_requests"],
                        name="estimated_spend", display_name="Estimated spend",
                        formula="SUM(estimated_cost)", position=1, precision=2,
                        prefix="₱")
    await ensure_metric(w.db, w.report, w.lists["facility_requests"],
                        name="average_cost", display_name="Average request",
                        formula="AVG(estimated_cost)", position=2, precision=2,
                        prefix="₱")

    await ensure_view(
        w.db, w.report, w.lists["staff_directory"],
        name="by_employment", display_name="By employment type", position=1,
        config={"group_by": _group_by(w, "staff_directory", "employment_type")},
    )
    await ensure_metric(w.db, w.report, w.lists["staff_directory"],
                        name="headcount", display_name="Headcount", formula="COUNT()")
    await ensure_metric(w.db, w.report, w.lists["staff_directory"],
                        name="total_load", display_name="Weekly teaching load",
                        formula="SUM(load_hours)", position=1, suffix=" h")
    await ensure_view(
        w.db, w.report, w.lists["facilities"],
        name="out_of_service", display_name="Out of service", position=1,
        config={"filters": _filter(w, "facilities", ("serviceable", "is", "false")),
                "sorts": _sorts(w, "facilities", ("code", "asc"))},
    )
    await ensure_metric(w.db, w.report, w.lists["facilities"],
                        name="asset_count", display_name="Assets", formula="COUNT()")


# -- phase 7: sharing ------------------------------------------------------


async def build_sharing(w):
    """Who can see what.

    Every resource type a grant can target appears here — workspace, list, view,
    form and a single record — with both kinds of principal, and one grant that
    expires. The reference lists were already shared `lookup`-only in phase 2,
    because a lookup column cannot be built against a list its author cannot read.
    """
    registrar, itadmin = w.users["s.villanueva"], w.users["admin"]
    expires = datetime.now(timezone.utc) + timedelta(days=60)

    for actor, workspace, actions, principal in [
        (registrar, "academics", VIEWER, {"group": w.groups["teachers"]}),
        (registrar, "academics", MANAGER, {"group": w.groups["administration"]}),
        (registrar, "student_services", EDITOR, {"group": w.groups["student_support"]}),
        (registrar, "library", MANAGER, {"user": w.users["f.aquino"]}),
        (registrar, "library", VIEWER, {"group": w.groups["everyone"]}),
        (itadmin, "operations", [Action.view, Action.create, Action.edit],
         {"group": w.groups["administration"]}),
    ]:
        await grant(w.db, w.report, actor,
                    resource_type=ResourceType.workspace,
                    resource_uuid=w.workspaces[workspace].uuid,
                    actions=actions, **principal)

    for actor, list_name, actions, principal in [
        (registrar, "enrollments", EDITOR, {"group": w.groups["teachers"]}),
        (registrar, "students", VIEWER, {"group": w.groups["student_support"]}),
        (registrar, "clinic_visits", MANAGER, {"user": w.users["n.santos"]}),
        (registrar, "attendance", [Action.view, Action.create, Action.edit],
         {"group": w.groups["teachers"]}),
        # `counseling_cases` does not inherit its workspace's grants, so these two
        # are the only way in besides ownership.
        (registrar, "counseling_cases", MANAGER, {"user": w.users["d.okafor"]}),
        (registrar, "counseling_cases", VIEWER, {"user": w.users["m.reyes"]}),
        (itadmin, "staff_directory", VIEWER, {"group": w.groups["department_heads"]}),
        (itadmin, "facility_requests", [Action.view, Action.create],
         {"group": w.groups["everyone"]}),
    ]:
        await grant(w.db, w.report, actor,
                    resource_type=ResourceType.list,
                    resource_uuid=w.lists[list_name].uuid,
                    actions=actions, **principal)

    # Expiring: a substitute covering a term, and nothing to remember to revoke.
    await grant(w.db, w.report, registrar,
                resource_type=ResourceType.list, resource_uuid=w.lists["sections"].uuid,
                actions=[Action.view, Action.edit], user=w.users["t.reyes"],
                expires_at=expires)

    # A view shared on its own: the nurse sees the open guidance cases without the
    # casework list itself, which she has no grant on.
    for view in await ViewService.list(w.db, w.lists["counseling_cases"].id, registrar):
        if view.name == "open_cases":
            await grant(w.db, w.report, registrar,
                        resource_type=ResourceType.view, resource_uuid=view.uuid,
                        actions=VIEWER, user=w.users["n.santos"])

    intake = await _find_form(w, "students", "admissions_intake")
    if intake is not None:
        await grant(w.db, w.report, registrar,
                    resource_type=ResourceType.form, resource_uuid=intake.uuid,
                    actions=VIEWER, group=w.groups["registrar_s_office"])

    # One record, shared by itself. This works over the API; the UI cannot yet
    # render a record whose list the viewer cannot open — see docs/known-gaps.md.
    first_case = w.row("counseling_cases", 0)
    if first_case is not None:
        await grant(w.db, w.report, registrar,
                    resource_type=ResourceType.item, resource_uuid=first_case,
                    actions=VIEWER, user=w.users["c.delacruz"])


async def _find_form(w, list_name, form_name):
    from sqlalchemy import select

    from src.lists.forms.models import Form

    return (
        await w.db.execute(
            select(Form).where(
                Form.list_id == w.lists[list_name].id,
                Form.name == form_name,
                Form.deleted_at.is_(None),
            )
        )
    ).scalars().first()


# -- phase 8: the lived-in bits --------------------------------------------


async def build_flourishes(w):
    """The states a tenant reaches by being used rather than by being set up.

    Things in bins, and a list handed to someone else. Without these the demo shows
    only the happy path, and the bins, the restore flow and the "Incoming"
    workspace all read as empty features.
    """
    registrar = w.users["s.villanueva"]

    # A few records in bins, recoverable for the retention window.
    binned_requests = [uuid for uuid in (w.rows.get("facility_requests") or [])[-2:]]
    if binned_requests:
        await bin_records(w.db, w.report, w.lists["facility_requests"], binned_requests)
    last_student = w.row("students", len(w.rows.get("students") or []) - 1)
    if last_student is not None:
        await bin_records(w.db, w.report, w.lists["students"], [last_student])

    # A binned column: its values stay in `data` and come back if it is restored.
    remarks = w.columns["sections"].get("remarks")
    if remarks is not None and remarks.deleted_at is None:
        if await ColumnService.delete(w.db, w.lists["sections"], remarks.uuid):
            w.report["columns binned"] += 1

    # A binned view.
    for view in await ViewService.list(w.db, w.lists["catalog"].id,
                                       w.users["f.aquino"]):
        if view.name == "reading_room":
            if await ViewService.delete(w.db, w.lists["catalog"].id, view.uuid):
                w.report["views binned"] += 1

    # A binned list, so the workspace's bin is not empty either. Checked against
    # binned rows as well as live ones: `ensure_list` ignores the bin (a name is
    # unique only among live lists), so without this every run would make another.
    academics = w.workspaces["academics"]
    if not await list_exists(w.db, academics.id, "pilot_electives"):
        pilot = await ensure_list(
            w.db, w.report, academics,
            name="pilot_electives", display_name="Pilot Electives (2025)",
            description="Last year's elective trial. Binned, not yet purged.",
        )
        if await ListService.delete(w.db, pilot.uuid):
            w.report["lists binned"] += 1

    # A transfer, which is what mints the recipient's immutable "Incoming"
    # workspace: ownership flows through the workspace, so handing a list over is a
    # move into one the recipient owns. Recognised on a re-run by asking whether the
    # recipient already has it — it is no longer where it was created.
    principal = w.users["m.reyes"]
    if not await list_owned_by(w.db, principal, "alumni_directory"):
        alumni = await ensure_list(
            w.db, w.report, academics,
            name="alumni_directory", display_name="Alumni Directory",
            description="Handed to the principal's office; landed in their Incoming "
                        "workspace.",
        )
        name_column = await ensure_column(
            w.db, w.report, alumni, registrar,
            name="full_name", display_name="Name", type="text", position=0,
            config={"required": True},
        )
        await set_primary_column(w.db, alumni, name_column)
        await ListService.transfer_to_user(w.db, alumni.uuid, principal.id)
        w.report["lists transferred"] += 1


# -- entry point -----------------------------------------------------------


async def build(password: str) -> Report:
    report = Report()
    rng = random.Random(RNG_SEED)

    async with db_module.SessionLocal() as db:
        w = World(db, report, rng)
        for label, phase in [
            ("principals", build_principals),
            ("reference data", build_reference),
            ("academics", build_academics),
            ("student services", build_student_services),
            ("library", build_library),
            ("operations", build_operations),
            ("sharing", build_sharing),
            ("bins and transfers", build_flourishes),
        ]:
            print(f"  {label} …", flush=True)
            if phase is build_principals:
                await phase(w, password)
            else:
                await phase(w)

    return report


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Westbrook Academy demo scenario."
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"password for every seeded account (default {DEFAULT_PASSWORD!r})",
    )
    args = parser.parse_args()

    print("Building Westbrook Academy …")
    report = await build(args.password)
    print("\nDone.\n")
    print(report.render())
    print(
        "\nSign in as admin (superuser), s.villanueva (owns four workspaces), "
        "\nm.reyes, d.okafor, f.aquino, l.tan, n.santos, …"
        f"\nPassword for every account: {args.password}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
