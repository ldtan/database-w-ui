"""Signing out has to end the session on the server, not only in the browser.

Clearing the token pair from a browser's storage ends nothing: the refresh token stays
valid for REFRESH_TOKEN_EXPIRE_DAYS, so anything that recovers it afterwards — a shared
machine, a profile backup — still holds a live session that the sign-out did not touch.
`POST /auth/tokens/revoke` is what makes it stop working, and these pin down the three
properties the SPA's sign-out depends on: it works, it is narrow, and it cannot fail in
a way that leaves someone unable to sign out.
"""
from src.auth.tokens import throttle
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService


async def _make_user(session_factory, name: str, password: str = "origpw123"):
    async with session_factory() as db:
        return await UserService.create(
            db,
            UserCreate(username=name, email=f"{name}@example.com", password=password),
        )


async def _login(client, username: str, password: str = "origpw123") -> dict:
    resp = await client.post(
        "/api/auth/tokens/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _refresh(client, token: str):
    return await client.post(
        "/api/auth/tokens/refresh", json={"refresh_token": token}
    )


async def _revoke(client, token: str, **kwargs):
    return await client.post(
        "/api/auth/tokens/revoke", json={"refresh_token": token}, **kwargs
    )


async def test_a_revoked_refresh_token_stops_working(client, session_factory):
    await _make_user(session_factory, "leaver")
    tokens = await _login(client, "leaver")

    # It works right up until the revoke, so the refusal below is the revoke's doing
    # and not an expiry or a malformed token.
    first = await _refresh(client, tokens["refresh_token"])
    assert first.status_code == 200, first.text
    rotated = first.json()["refresh_token"]

    assert (await _revoke(client, rotated)).status_code == 204
    after = await _refresh(client, rotated)
    assert after.status_code == 401, after.text


async def test_signing_out_does_not_need_a_live_access_token(client, session_factory):
    """The session most in need of a sign-out is one already past its access token.

    Requiring authentication here would make that case the one case that cannot sign
    out — so the route takes the refresh token as its own authorisation and no header
    at all.
    """
    await _make_user(session_factory, "expired")
    tokens = await _login(client, "expired")

    resp = await client.post(
        "/api/auth/tokens/revoke",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": ""},
    )
    assert resp.status_code == 204, resp.text
    assert (await _refresh(client, tokens["refresh_token"])).status_code == 401


async def test_revoking_one_session_leaves_the_others_alone(client, session_factory):
    """Signing out of one browser must not sign the account out everywhere.

    Each login stores its own row, so a revoke is per-token. If this ever fails, the
    revoke has started matching on the user instead.
    """
    await _make_user(session_factory, "twodevices")
    laptop = await _login(client, "twodevices")
    phone = await _login(client, "twodevices")

    assert (await _revoke(client, laptop["refresh_token"])).status_code == 204

    assert (await _refresh(client, laptop["refresh_token"])).status_code == 401
    still_live = await _refresh(client, phone["refresh_token"])
    assert still_live.status_code == 200, still_live.text


async def test_an_unknown_token_is_accepted_rather_than_answered(client):
    """204 whether or not the token existed.

    A sign-out has nothing to do with the difference, and answering it would make this
    an oracle for whether a token someone holds is still live — the exact question an
    attacker with a stolen token wants answered.
    """
    assert (await _revoke(client, "not-a-token-anyone-issued")).status_code == 204


async def test_revoking_twice_is_not_an_error(client, session_factory):
    """The SPA fires this without waiting for it, so a retry or a double-click must
    not produce an error the user sees."""
    await _make_user(session_factory, "doubleclick")
    tokens = await _login(client, "doubleclick")

    assert (await _revoke(client, tokens["refresh_token"])).status_code == 204
    assert (await _revoke(client, tokens["refresh_token"])).status_code == 204


async def test_a_revoked_token_does_not_count_against_the_login_throttle(
    client, session_factory
):
    """Signing out is not a failed sign-in. If the revoke ever routed through the
    login path, a user signing out repeatedly would lock themselves out."""
    await _make_user(session_factory, "quiet")
    tokens = await _login(client, "quiet")
    throttle.clear_failures("quiet")

    for _ in range(6):
        assert (await _revoke(client, tokens["refresh_token"])).status_code == 204

    assert (await _login(client, "quiet"))["access_token"]
