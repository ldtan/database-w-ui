"""The liveness probe a host health-checks.

It is the one endpoint that must answer without a database and without a token,
so both are asserted here: this client has no Authorization header, and no test
database is wired into the app.
"""

from httpx import ASGITransport, AsyncClient

from main import create_app


async def test_health_needs_neither_auth_nor_a_database():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
