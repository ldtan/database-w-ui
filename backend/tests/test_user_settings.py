"""Per-account UI preferences.

Theme and light/dark used to live only in localStorage, so they were a property of
the browser rather than the account. These cover the two properties that make the
move to the server safe: a write merges rather than replaces (so one tab cannot
undo another) and refuses anything it cannot store, and a read tolerates keys and
values it does not recognise (so a rollback does not turn every /me into a 500).
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module


SETTINGS_URL = "/api/auth/users/me/settings"


async def _make_user(db, name: str):
    return await UserService.create(
        db, UserCreate(username=name, email=f"{name}@example.com", password="origpw123")
    )


@pytest_asyncio.fixture
def make_client(engine, session_factory):
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


async def test_a_fresh_account_reads_the_defaults(make_client, session_factory):
    """The column starts as {}, and must present as the same theme a fresh browser
    would pick — otherwise a first login would visibly change what the user sees."""
    async with session_factory() as db:
        user = await _make_user(db, "newcomer")

    async with make_client(user) as client:
        resp = await client.get("/api/auth/users/me")
        assert resp.status_code == 200
        assert resp.json()["settings"] == {
            "theme": "modern",
            "mode": "light",
            "timezone": "UTC",
        }


async def test_settings_survive_a_new_session(make_client, session_factory):
    """The whole point: saved on one device, returned to the next one that logs in."""
    async with session_factory() as db:
        user = await _make_user(db, "roamer")

    async with make_client(user) as client:
        resp = await client.patch(
            SETTINGS_URL, json={"theme": "brutalist", "mode": "dark"}
        )
        assert resp.status_code == 200
        assert resp.json()["settings"] == {
            "theme": "brutalist",
            "mode": "dark",
            "timezone": "UTC",
        }

    # A separate client, standing in for a second device.
    async with session_factory() as db:
        reloaded = await UserService.get_by_username(db, "roamer")

    async with make_client(reloaded) as client:
        resp = await client.get("/api/auth/users/me")
        assert resp.json()["settings"] == {
            "theme": "brutalist",
            "mode": "dark",
            "timezone": "UTC",
        }


async def test_a_write_merges_and_does_not_replace(make_client, session_factory):
    """Two tabs changing different preferences must not clobber each other."""
    async with session_factory() as db:
        user = await _make_user(db, "merger")

    async with make_client(user) as client:
        await client.patch(SETTINGS_URL, json={"theme": "luxury"})

        # Sends only `mode`. If this replaced the blob, `theme` would fall back to
        # the default and the earlier choice would be silently lost.
        resp = await client.patch(SETTINGS_URL, json={"mode": "dark"})
        assert resp.status_code == 200
        assert resp.json()["settings"] == {
            "theme": "luxury",
            "mode": "dark",
            "timezone": "UTC",
        }


async def test_an_unknown_key_is_rejected(make_client, session_factory):
    """A typo'd key should fail loudly rather than be stored and never read."""
    async with session_factory() as db:
        user = await _make_user(db, "typist")

    async with make_client(user) as client:
        resp = await client.patch(SETTINGS_URL, json={"them": "luxury"})
        assert resp.status_code == 422

        # And a known key with a value outside the union.
        resp = await client.patch(SETTINGS_URL, json={"theme": "vaporwave"})
        assert resp.status_code == 422

        # Neither attempt left anything behind.
        resp = await client.get("/api/auth/users/me")
        assert resp.json()["settings"] == {
            "theme": "modern",
            "mode": "light",
            "timezone": "UTC",
        }


async def test_a_null_value_is_rejected(make_client, session_factory):
    """Null is how the merge spells "not sent", so it must not be storable.

    The blob has no room for one: every read validates against a non-optional union,
    so a stored null would 500 not just /me but the all-users listing any signed-in
    caller hits — one malformed request degrading the app for everybody.
    """
    async with session_factory() as db:
        user = await _make_user(db, "nuller")

    async with make_client(user) as client:
        resp = await client.patch(SETTINGS_URL, json={"theme": None})
        assert resp.status_code == 422

        # Alongside a valid key, so the whole write is refused rather than half-applied.
        resp = await client.patch(SETTINGS_URL, json={"theme": None, "mode": "dark"})
        assert resp.status_code == 422

        resp = await client.get("/api/auth/users/me")
        assert resp.status_code == 200
        assert resp.json()["settings"] == {
            "theme": "modern",
            "mode": "light",
            "timezone": "UTC",
        }


async def test_a_read_tolerates_an_unrecognised_stored_value(
    make_client, session_factory
):
    """The likelier shape of a rollback than an unknown key: a deploy that adds a
    palette writes an existing key with a value this build has no name for."""
    async with session_factory() as db:
        user = await _make_user(db, "futuretheme")
        user.settings = {"theme": "sunset", "mode": "dark"}
        await db.commit()

    async with make_client(user) as client:
        resp = await client.get("/api/auth/users/me")
        assert resp.status_code == 200
        # The unreadable key falls back to its default; the readable one is untouched.
        assert resp.json()["settings"] == {
            "theme": "modern",
            "mode": "dark",
            "timezone": "UTC",
        }


async def test_a_read_tolerates_an_unrecognised_stored_key(
    make_client, session_factory
):
    """A newer deploy can write a key this build has never heard of. After a
    rollback that row still has to serialize, or the user cannot load the app."""
    async with session_factory() as db:
        user = await _make_user(db, "rolledback")
        # Assigning a new dict, not mutating: JSONB is not mutation-tracked.
        user.settings = {"theme": "nordic", "sidebar_pinned": True}
        await db.commit()
        user_id = user.id

    async with make_client(user) as client:
        resp = await client.get("/api/auth/users/me")
        assert resp.status_code == 200
        assert resp.json()["settings"] == {
            "theme": "nordic",
            "mode": "light",
            "timezone": "UTC",
        }

        # And a write from the older build preserves the key it cannot see, rather
        # than dropping it — the merge is what makes the rollback non-destructive.
        resp = await client.patch(SETTINGS_URL, json={"mode": "dark"})
        assert resp.status_code == 200

    async with session_factory() as db:
        stored = await db.get(User, user_id)
        assert stored.settings == {
            "theme": "nordic",
            "mode": "dark",
            "sidebar_pinned": True,
        }


async def test_a_timezone_round_trips(make_client, session_factory):
    """Unlike theme and mode this one is read by the *backend*, to stamp `today` and
    `now` column defaults — so what a browser reports has to come back intact."""
    async with session_factory() as db:
        user = await _make_user(db, "traveller")

    async with make_client(user) as client:
        resp = await client.patch(SETTINGS_URL, json={"timezone": "Asia/Manila"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["settings"]["timezone"] == "Asia/Manila"

        # And a later move overwrites it: the browser is the authority on where the
        # user is, so re-reporting is how the stored value stays true.
        resp = await client.patch(SETTINGS_URL, json={"timezone": "Europe/Lisbon"})
        assert resp.json()["settings"]["timezone"] == "Europe/Lisbon"
        # Untouched keys survive, as for any other setting.
        assert resp.json()["settings"]["theme"] == "modern"


async def test_an_unknown_timezone_is_rejected(make_client, session_factory):
    """No Literal to lean on here, so the check is explicit — and it matters more
    than for theme: an unresolvable zone does not fail visibly, it silently sends
    every `today` default back to UTC."""
    async with session_factory() as db:
        user = await _make_user(db, "lost")

    async with make_client(user) as client:
        for bad in ["Mars/Olympus_Mons", "+08:00", "PST", ""]:
            resp = await client.patch(SETTINGS_URL, json={"timezone": bad})
            assert resp.status_code == 422, f"{bad!r}: {resp.text}"

        resp = await client.get("/api/auth/users/me")
        assert resp.json()["settings"]["timezone"] == "UTC"


async def test_a_read_tolerates_an_unrecognised_stored_timezone(
    make_client, session_factory
):
    """A zone that resolved on the machine that wrote it need not resolve on the one
    reading it — a build of zoneinfo without the `backward` link set drops the older
    aliases, and tzdata retires ids between releases. /me must still load.
    """
    async with session_factory() as db:
        user = await _make_user(db, "stale")
        user.settings = {"theme": "nordic", "timezone": "Europe/Atlantis"}
        await db.commit()

    async with make_client(user) as client:
        resp = await client.get("/api/auth/users/me")
        assert resp.status_code == 200
        # Falls back to the default rather than serving a zone nothing can resolve —
        # and the frontend reports a live one on load, so it is corrected in-session.
        assert resp.json()["settings"]["timezone"] == "UTC"
        assert resp.json()["settings"]["theme"] == "nordic"


async def test_settings_are_per_account(make_client, session_factory):
    """The route takes no uuid, so it can only ever write the caller's own row."""
    async with session_factory() as db:
        one = await _make_user(db, "userone")
        two = await _make_user(db, "usertwo")

    async with make_client(one) as client:
        await client.patch(SETTINGS_URL, json={"theme": "luxury", "mode": "dark"})

    async with make_client(two) as client:
        resp = await client.get("/api/auth/users/me")
        assert resp.json()["settings"] == {
            "theme": "modern",
            "mode": "light",
            "timezone": "UTC",
        }
