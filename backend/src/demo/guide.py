"""The Contacts example the guide talks about, built for real.

    uv run python -m src.demo.guide
    uv run python -m src.demo.guide --password letmein

`frontend/scripts/capture-guide.mjs` photographs this tenant, and the pictures land
in the guide beside the paragraphs describing it. That is the whole reason it
exists as a scenario of its own rather than a corner of `school`: a reader who is
told to make a *Sales* workspace and a *Contacts* list should not be shown a
screenshot of Westbrook Academy's enrollments. Every name here is one the guide's
prose already puts in the reader's head.

**Changing this changes the screenshots, and only re-running the capture proves
it.** The two are coupled through the labels the capture script clicks — "Contacts",
"Status", "Customers only" — so a rename here is a broken capture run, not a
broken app. `frontend/scripts/capture-guide.mjs` names the ones it depends on.

It is small on purpose. Enough records to make a table look inhabited, one lookup
chain (Continents are overkill; Countries and Cities carry the nesting story), one
calculated column, three metrics, one saved view, one colleague to share with, and
a couple of binned records so the bin is not empty in its own screenshot. Anything
more is another thing to keep true.
"""
import argparse
import asyncio
from datetime import date

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.auth.seed import ensure_system_groups
from src.infrastructure import database as db_module
from src.permissions.enums import Action, ResourceType

from ._builders import (
    Report,
    bin_records,
    ensure_column,
    ensure_group,
    ensure_list,
    ensure_metric,
    ensure_person,
    ensure_user,
    ensure_view,
    ensure_workspace,
    grant,
    seed_records,
    set_primary_column,
    set_settings,
)

# One password for every account here, as in `school`: a demo tenant is not a
# secret, and one the operator has to look up account by account is one nobody
# signs into.
DEFAULT_PASSWORD = "guidedemo2026"

# The scenario's "now". Birthdays sit decades behind it and nothing is dated ahead
# of it — a birth date in the future is refused outright.
TODAY = date(2026, 8, 2)

EDITOR = [Action.view, Action.create, Action.edit, Action.delete]
VIEWER = [Action.view]


# -- the reference lists ---------------------------------------------------

COUNTRIES = ["France", "Japan", "Philippines", "United Kingdom"]

# city -> the country it sits in. What makes the nested lookup demonstrable: the
# guide's "pick France and the city box only offers French cities" is this table.
CITIES = [
    ("Paris", "France"),
    ("Lyon", "France"),
    ("Tokyo", "Japan"),
    ("Kyoto", "Japan"),
    ("Manila", "Philippines"),
    ("Cebu", "Philippines"),
    ("London", "United Kingdom"),
    ("Bristol", "United Kingdom"),
]

# full_name, email, status, birthday, country, city, deal_value, notes
CONTACTS = [
    ("Ada Lovelace", "ada@analytical.example", "Customer", (1985, 12, 10),
     "United Kingdom", "London", 4800, "Renewed early. Wants the annual plan."),
    ("Bruno Mercier", "bruno@mercier.example", "Contacted", (1979, 3, 22),
     "France", "Lyon", 1250, "Asked for a second quote."),
    ("Chiaki Nakamura", "chiaki@nakamura.example", "Customer", (1990, 7, 4),
     "Japan", "Tokyo", 9200, "Introduced us to two other teams."),
    ("Delia Ramos", "delia@ramos.example", "New", (1994, 1, 30),
     "Philippines", "Manila", None, None),
    ("Émile Duval", "emile@duval.example", "Customer", (1982, 9, 15),
     "France", "Paris", 3300, "Prefers email to calls."),
    ("Faye Robinson", "faye@robinson.example", "Contacted", (1988, 5, 8),
     "United Kingdom", "Bristol", 750, None),
    ("Goro Tanaka", "goro@tanaka.example", "New", (1996, 11, 2),
     "Japan", "Kyoto", None, "Came in from the spring mailing."),
    ("Hannah Cruz", "hannah@cruz.example", "Customer", (1991, 2, 19),
     "Philippines", "Cebu", 5600, "Three sites, one invoice."),
    ("Isabelle Fontaine", "isabelle@fontaine.example", "Contacted", (1986, 6, 27),
     "France", "Paris", 2100, None),
    ("Jonah Whitfield", "jonah@whitfield.example", "New", (1999, 4, 12),
     "United Kingdom", "London", None, None),
    ("Keiko Arai", "keiko@arai.example", "Customer", (1975, 8, 3),
     "Japan", "Tokyo", 7400, "Longest-running account."),
    ("Luis Bautista", "luis@bautista.example", "Contacted", (1993, 10, 21),
     "Philippines", "Manila", 1800, "Waiting on their budget cycle."),
]

# Records to soft-delete afterwards, so the "Deleted records" screenshot has
# something in it. Named rather than indexed: an index is a silent liar the day
# someone inserts a contact above it.
BINNED = ["Jonah Whitfield", "Goro Tanaka"]

CAMPAIGNS = [
    ("Spring mailing", "Done", 1200),
    ("Referral push", "Running", 800),
    ("Autumn webinar", "Planned", 1500),
]


async def build(password: str) -> Report:
    report = Report()

    async with db_module.SessionLocal() as db:
        await ensure_system_groups(db)

        # -- the people -------------------------------------------------
        # Two accounts, because half the sharing story is a row in a table with
        # somebody else's name in it. The owner is deliberately not `admin`: the
        # screenshots should show what an ordinary account sees, and an admin's
        # People page carries buttons nobody else has.
        owner = await ensure_user(
            db, report,
            username="guide.demo", email="guide.demo@example.com", password=password,
        )
        await ensure_person(
            db, report, owner,
            first_name="Robin", last_name="Fields", birth_date=date(1987, 4, 9),
        )
        colleague = await ensure_user(
            db, report,
            username="guide.colleague", email="guide.colleague@example.com",
            password=password,
        )
        await ensure_person(
            db, report, colleague,
            first_name="Sam", last_name="Okonjo", birth_date=date(1992, 12, 1),
        )
        sales_team = await ensure_group(
            db, report,
            name="sales_team", display_name="Sales Team",
            description="Everyone working the pipeline.",
            members=[owner, colleague],
        )

        # The capture runs in the default palette, light and dark. Pinning it here
        # means the screenshots do not change colour because somebody clicked the
        # palette button while exploring the demo.
        await set_settings(db, owner, theme="modern", mode="light")

        # -- the workspaces ---------------------------------------------
        # Two, so the Workspaces page and the nav tree photograph as somewhere
        # with things in it rather than as an empty state.
        sales = await ensure_workspace(
            db, report, owner,
            name="sales", display_name="Sales",
            description="Everything to do with customers and the pipeline.",
        )
        marketing = await ensure_workspace(
            db, report, owner,
            name="marketing", display_name="Marketing",
            description="Campaigns, and what came of them.",
        )

        # -- reference lists, innermost first ---------------------------
        # Cities points at Countries, and Contacts points at both; each has to
        # exist before the column that names it.
        countries = await ensure_list(
            db, report, sales,
            name="countries", display_name="Countries",
            description="Reference list. Shared for lookup only.",
        )
        country_name = await ensure_column(
            db, report, countries, owner,
            name="country_name", display_name="Country", type="text", position=0,
            config={"required": True, "unique": True},
        )
        await set_primary_column(db, countries, country_name)
        await seed_records(
            db, report, countries, owner, {"country_name": country_name},
            [{"country_name": name} for name in COUNTRIES],
        )

        cities = await ensure_list(
            db, report, sales,
            name="cities", display_name="Cities",
            description="Reference list. Each city says which country it is in.",
        )
        city_name = await ensure_column(
            db, report, cities, owner,
            name="city_name", display_name="City", type="text", position=0,
            config={"required": True},
        )
        # The column that makes the nesting possible: without a link from Cities
        # back to Countries there is nothing for "Match on" to offer.
        city_country = await ensure_column(
            db, report, cities, owner,
            name="country", display_name="Country", type="lookup", position=1,
            config={"target_list_uuid": countries.uuid},
        )
        await set_primary_column(db, cities, city_name)
        await seed_records(
            db, report, cities, owner,
            {"city_name": city_name, "country": city_country},
            [{"city_name": city, "country": country} for city, country in CITIES],
        )

        # -- Contacts, the list the guide walks you through -------------
        contacts = await ensure_list(
            db, report, sales,
            name="contacts", display_name="Contacts",
            description="People we have talked to, and where each one got to.",
        )
        cols = {}
        cols["full_name"] = await ensure_column(
            db, report, contacts, owner,
            name="full_name", display_name="Full name", type="text", position=0,
            config={"required": True},
        )
        cols["email"] = await ensure_column(
            db, report, contacts, owner,
            name="email", display_name="Email", type="text", position=1,
            config={"unique": True},
        )
        cols["status"] = await ensure_column(
            db, report, contacts, owner,
            name="status", display_name="Status", type="choice", position=2,
            config={"options": ["New", "Contacted", "Customer"], "default": "New"},
        )
        cols["birthday"] = await ensure_column(
            db, report, contacts, owner,
            name="birthday", display_name="Birthday", type="date", position=3,
            config={"max": TODAY.isoformat()},
        )
        cols["country"] = await ensure_column(
            db, report, contacts, owner,
            name="country", display_name="Country", type="lookup", position=4,
            config={"target_list_uuid": countries.uuid},
        )
        # The nested one. `parent_column_uuid` is the sibling whose choice narrows
        # this; `target_filter_column_uuid` is the column on *Cities* that says
        # which country a city belongs to. Both or neither — the service checks
        # the pairing.
        cols["city"] = await ensure_column(
            db, report, contacts, owner,
            name="city", display_name="City", type="lookup", position=5,
            config={
                "target_list_uuid": cities.uuid,
                "parent_column_uuid": cols["country"].uuid,
                "target_filter_column_uuid": city_country.uuid,
            },
        )
        cols["deal_value"] = await ensure_column(
            db, report, contacts, owner,
            name="deal_value", display_name="Deal value", type="number", position=6,
            config={"number_format": "currency", "precision": 2, "min": 0},
        )
        cols["notes"] = await ensure_column(
            db, report, contacts, owner,
            name="notes", display_name="Notes", type="text", position=7,
            config={"text_mode": "multiline"},
        )
        # The guide's worked example, near enough: a formula over the columns
        # beside it, with a ROUND so the screenshot shows sensible money.
        cols["commission"] = await ensure_column(
            db, report, contacts, owner,
            name="commission", display_name="Commission", type="calculated",
            position=8, config={"formula": "ROUND(deal_value * 0.15, 2)"},
        )
        await set_primary_column(db, contacts, cols["full_name"])

        rows = await seed_records(
            db, report, contacts, owner, cols,
            [
                {
                    "full_name": full_name,
                    "email": email,
                    "status": status,
                    "birthday": date(*born).isoformat(),
                    "country": country,
                    "city": city,
                    "deal_value": deal_value,
                    "notes": notes,
                }
                for full_name, email, status, born, country, city, deal_value, notes
                in CONTACTS
            ],
        )

        # A saved view, so the View dropdown has something to drop down to and the
        # guide's "Customers only, sorted by name" is a real thing on screen.
        await ensure_view(
            db, report, contacts,
            name="customers", display_name="Customers only", position=1,
            description="Everyone who has bought something, by name.",
            config={
                "filters": {
                    "conjunction": "and",
                    "conditions": [
                        {
                            "column_uuid": str(cols["status"].uuid),
                            "operator": "eq",
                            "value": "Customer",
                        }
                    ],
                },
                "sorts": [{"column_uuid": str(cols["full_name"].uuid),
                           "direction": "asc"}],
            },
        )

        for position, (name, display_name, formula, prefix) in enumerate([
            ("pipeline", "Pipeline value", "SUM(deal_value)", "$"),
            ("average_deal", "Average deal", "ROUND(AVG(deal_value), 2)", "$"),
            ("people", "Contacts", "COUNT()", None),
        ]):
            await ensure_metric(
                db, report, contacts,
                name=name, display_name=display_name, formula=formula,
                position=position, prefix=prefix,
            )

        # -- a second workspace with something in it --------------------
        campaigns = await ensure_list(
            db, report, marketing,
            name="campaigns", display_name="Campaigns",
            description="What we sent, and what it cost.",
        )
        campaign_cols = {}
        campaign_cols["title"] = await ensure_column(
            db, report, campaigns, owner,
            name="title", display_name="Campaign", type="text", position=0,
            config={"required": True},
        )
        campaign_cols["stage"] = await ensure_column(
            db, report, campaigns, owner,
            name="stage", display_name="Stage", type="choice", position=1,
            config={"options": ["Planned", "Running", "Done"]},
        )
        campaign_cols["budget"] = await ensure_column(
            db, report, campaigns, owner,
            name="budget", display_name="Budget", type="number", position=2,
            config={"number_format": "currency", "precision": 2},
        )
        await set_primary_column(db, campaigns, campaign_cols["title"])
        await seed_records(
            db, report, campaigns, owner, campaign_cols,
            [
                {"title": title, "stage": stage, "budget": budget}
                for title, stage, budget in CAMPAIGNS
            ],
        )

        # -- something in "Shared with me" ------------------------------
        # Owned by the colleague and handed back, because the rail's "Shared with
        # me" branch and the dashboard's shared-lists card are both in shot and
        # both read as broken when empty. It has to come from the *other* account:
        # a list you own is never shared with you.
        partners = await ensure_workspace(
            db, report, colleague,
            name="partners", display_name="Partners",
            description="Sam's workspace. Only the one list is shared out of it.",
        )
        resellers = await ensure_list(
            db, report, partners,
            name="resellers", display_name="Resellers",
            description="Who sells for us, and where.",
        )
        reseller_cols = {}
        reseller_cols["company"] = await ensure_column(
            db, report, resellers, colleague,
            name="company", display_name="Company", type="text", position=0,
            config={"required": True},
        )
        reseller_cols["region"] = await ensure_column(
            db, report, resellers, colleague,
            name="region", display_name="Region", type="choice", position=1,
            config={"options": ["Europe", "Asia", "Americas"]},
        )
        await set_primary_column(db, resellers, reseller_cols["company"])
        await seed_records(
            db, report, resellers, colleague, reseller_cols,
            [
                {"company": "Northgate Supply", "region": "Europe"},
                {"company": "Pacific Rim Trading", "region": "Asia"},
                {"company": "Cordillera Partners", "region": "Americas"},
            ],
        )
        await grant(
            db, report, colleague,
            resource_type=ResourceType.list, resource_uuid=resellers.uuid,
            actions=VIEWER, user=owner,
        )

        # -- sharing, so the permissions screenshot has rows ------------
        await grant(
            db, report, owner,
            resource_type=ResourceType.list, resource_uuid=contacts.uuid,
            actions=EDITOR, user=colleague,
        )
        await grant(
            db, report, owner,
            resource_type=ResourceType.workspace, resource_uuid=marketing.uuid,
            actions=VIEWER, group=sales_team,
        )
        # The reference lists are the case the guide makes for `lookup` on its own:
        # pickable in someone else's column, not browsable as a list.
        for reference in (countries, cities):
            await grant(
                db, report, owner,
                resource_type=ResourceType.list, resource_uuid=reference.uuid,
                actions=[Action.lookup], group=sales_team,
            )

        # -- and a non-empty bin ----------------------------------------
        wanted = [
            uuid
            for uuid, contact in zip(rows, CONTACTS)
            if contact[0] in BINNED
        ]
        await bin_records(db, report, contacts, wanted)

    return report


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the guide's Contacts demo tenant."
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"password for every seeded account (default {DEFAULT_PASSWORD!r})",
    )
    args = parser.parse_args()

    print("Building the guide demo …")
    report = await build(args.password)
    print("\nDone.\n")
    print(report.render())
    print(
        "\nSign in as guide.demo (owns Sales and Marketing) or guide.colleague "
        f"(shared with).\nPassword for both: {args.password}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
