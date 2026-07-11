"""Regression guards for the database session configuration.

A production-vs-test mismatch on ``expire_on_commit`` once let a MissingGreenlet
500 slip through the whole suite: reading a mapped attribute after ``commit()``
lazy-reloads, and under asyncpg that IO happens outside the greenlet and raises.
The app was broken while every test stayed green because the tests used their own
session factory. These tests lock the setting and reproduce the exact failure
mode through the app's own factory.
"""
from src.infrastructure.database import SessionLocal, make_session_factory
from src.workspaces.models import Workspace


def test_sessionlocal_does_not_expire_on_commit():
    # Must stay False; see infrastructure/database.py for why.
    assert SessionLocal.kw.get("expire_on_commit") is False


def test_make_session_factory_config(engine):
    # The single place session config lives — app and tests both go through it,
    # so the two can never drift again.
    factory = make_session_factory(engine)
    assert factory.kw.get("expire_on_commit") is False


async def test_attribute_access_after_commit_does_not_raise(session_factory, user_id):
    """The behavioral guard: build an object, commit, then read a mapped
    attribute. With expire_on_commit=True this re-expires and raises
    MissingGreenlet under asyncpg; with False it simply returns the value."""
    async with session_factory() as db:
        ws = Workspace(owner_id=user_id, name="probe", display_name="Probe")
        db.add(ws)
        await db.commit()
        assert ws.name == "probe"  # would raise if the instance re-expired
