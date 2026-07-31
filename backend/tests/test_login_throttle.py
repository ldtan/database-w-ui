"""Login is the only route that answers before the caller has proved anything.

Left unlimited it offers a public URL two things at once: unlimited guessing at
`admin`, a username the deploy blueprint names out loud, and — because every
attempt spends an argon2 verification by design — a way to burn the single
instance's CPU with nothing but a wordlist. These cover both.
"""
import pytest

from src.auth.tokens import throttle
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.core.config import settings


@pytest.fixture
def limits():
    """Small thresholds, so a test is a handful of requests rather than sixty."""
    original = (
        settings.LOGIN_MAX_FAILURES_PER_USERNAME,
        settings.LOGIN_MAX_FAILURES_PER_IP,
    )
    settings.LOGIN_MAX_FAILURES_PER_USERNAME = 3
    settings.LOGIN_MAX_FAILURES_PER_IP = 5
    yield
    (
        settings.LOGIN_MAX_FAILURES_PER_USERNAME,
        settings.LOGIN_MAX_FAILURES_PER_IP,
    ) = original


async def _make_user(session_factory, name: str, password: str = "origpw123"):
    async with session_factory() as db:
        return await UserService.create(
            db,
            UserCreate(username=name, email=f"{name}@example.com", password=password),
        )


async def _login(client, username: str, password: str, ip: str = "203.0.113.7"):
    return await client.post(
        "/api/auth/tokens/login",
        data={"username": username, "password": password},
        headers={"X-Forwarded-For": ip},
    )


async def test_guessing_a_password_stops_being_answered(
    client, session_factory, limits
):
    await _make_user(session_factory, "target")

    for _ in range(settings.LOGIN_MAX_FAILURES_PER_USERNAME):
        assert (await _login(client, "target", "wrong")).status_code == 401

    resp = await _login(client, "target", "wrong-again")
    assert resp.status_code == 429
    # Without this a client has nothing to back off by but a guess.
    assert int(resp.headers["Retry-After"]) > 0


async def test_the_lockout_outlasts_a_correct_password(client, session_factory, limits):
    """Deliberate: a locked username that still accepted the right password
    would tell an attacker the moment they found it, which is the one bit the
    limit exists to withhold. The cost is that the real owner waits out the
    window too."""
    await _make_user(session_factory, "locked", password="rightpw123")

    for _ in range(settings.LOGIN_MAX_FAILURES_PER_USERNAME):
        await _login(client, "locked", "wrong")

    assert (await _login(client, "locked", "rightpw123")).status_code == 429


async def test_capitalisation_does_not_buy_a_fresh_budget(
    client, session_factory, limits
):
    await _make_user(session_factory, "casey")

    for i in range(settings.LOGIN_MAX_FAILURES_PER_USERNAME):
        # Same account, spelled differently every time.
        spelling = "casey".upper() if i % 2 else "Casey"
        assert (await _login(client, spelling, "wrong")).status_code == 401

    assert (await _login(client, "casey", "wrong")).status_code == 429


async def test_signing_in_clears_the_username_tally(client, session_factory, limits):
    """Someone who mistypes twice and then gets it right is back to a full
    budget — otherwise ordinary fumbling accumulates into a lockout."""
    await _make_user(session_factory, "fumbler", password="rightpw123")

    assert (await _login(client, "fumbler", "wrong")).status_code == 401
    assert (await _login(client, "fumbler", "rightpw123")).status_code == 200

    # A full run of failures is available again; the earlier one is forgotten.
    for _ in range(settings.LOGIN_MAX_FAILURES_PER_USERNAME):
        assert (await _login(client, "fumbler", "wrong")).status_code == 401


async def test_spraying_across_usernames_is_caught_by_address(
    client, session_factory, limits
):
    """Each username stays under its own limit, so only the address bucket sees
    what is going on."""
    assert (
        settings.LOGIN_MAX_FAILURES_PER_IP
        > settings.LOGIN_MAX_FAILURES_PER_USERNAME
    )

    for i in range(settings.LOGIN_MAX_FAILURES_PER_IP):
        assert (await _login(client, f"nobody{i}", "wrong")).status_code == 401

    # A username never tried before, from the same address.
    assert (await _login(client, "someone-else", "wrong")).status_code == 429
    # ...but not from a different one.
    assert (
        await _login(client, "someone-else", "wrong", ip="198.51.100.4")
    ).status_code == 401


async def test_a_forged_forwarded_header_cannot_shed_the_address_bucket(
    client, session_factory, limits
):
    """Only the rightmost entry counts — the rest is whatever the caller sent."""
    for i in range(settings.LOGIN_MAX_FAILURES_PER_IP):
        resp = await client.post(
            "/api/auth/tokens/login",
            data={"username": f"nobody{i}", "password": "wrong"},
            # A fresh invented address prepended each time, real one last.
            headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
        )
        assert resp.status_code == 401

    resp = await client.post(
        "/api/auth/tokens/login",
        data={"username": "another", "password": "wrong"},
        headers={"X-Forwarded-For": "10.0.0.99, 203.0.113.7"},
    )
    assert resp.status_code == 429


async def test_zero_disables_a_bucket(client, session_factory):
    """The escape hatch for a deployment where every user shares one apparent
    address — the per-username limit carries on regardless."""
    original = settings.LOGIN_MAX_FAILURES_PER_IP
    settings.LOGIN_MAX_FAILURES_PER_IP = 0
    try:
        for i in range(original + 5):
            assert (await _login(client, f"nobody{i}", "wrong")).status_code == 401
    finally:
        settings.LOGIN_MAX_FAILURES_PER_IP = original


def test_the_window_reopens():
    """Time-dependent, so it is asserted against the throttle directly rather
    than by sleeping out a real fifteen minutes in the suite."""
    throttle.reset()
    for _ in range(settings.LOGIN_MAX_FAILURES_PER_USERNAME):
        throttle.record_failure("waiter", "203.0.113.7")
    assert throttle.retry_after_seconds("waiter", "203.0.113.7") is not None

    # Age every recorded failure past the window.
    window = settings.LOGIN_THROTTLE_WINDOW_MINUTES * 60
    for bucket in throttle._failures.values():
        for i in range(len(bucket)):
            bucket[i] -= window + 1

    assert throttle.retry_after_seconds("waiter", "203.0.113.7") is None
