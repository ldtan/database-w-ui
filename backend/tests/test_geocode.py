"""The address-search proxy behind the location picker.

Nothing here reaches the network. What is worth pinning is the *contract the picker
relies on*: this route does not fail. A location column is usable without geocoding —
type an address, drop a pin — so an unconfigured or unreachable geocoder has to come
back as `available: false` and a sentence, never as a 5xx that makes somebody's
address field appear broken because a volunteer-run service was busy.
"""
import httpx
import pytest

from src.core.config import settings
from src.geocode import services


@pytest.fixture(autouse=True)
def _clear_cache():
    """The cache is process-global, so a test that seeds it would leak into the next."""
    services._cache.clear()
    yield
    services._cache.clear()


@pytest.fixture
def no_wait(monkeypatch):
    """Drop the one-call-a-second spacing. It is real and deliberate (Nominatim's
    published limit), and it would make this file take a minute to say nothing."""
    monkeypatch.setattr(services, "_spacing", lambda: 0.0)


@pytest.fixture
def upstream(monkeypatch):
    """Stand in for the geocoder by replacing the `httpx.AsyncClient` *class*.

    Not by patching `AsyncClient.get`, which is the obvious thing and is wrong here:
    the test client is itself an `httpx.AsyncClient`, so patching the method hijacks
    the request this test is trying to make. Replacing the class binding is safe
    because the test client already exists and keeps its own type — only the service,
    which constructs one per call, gets the stand-in.
    """

    def _install(responder):
        class _Fake:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kwargs):
                return await responder(url, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _Fake)

    return _install


async def test_unconfigured_is_answered_not_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "GEOCODE_URL", "")
    resp = await client.get("/api/geocode/?q=cebu")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["results"] == []
    # A sentence the picker can show, telling somebody what they can still do.
    assert "pin" in body["detail"].lower()


async def test_an_unreachable_geocoder_is_answered_not_refused(
    client, upstream, no_wait
):
    async def _boom(*args, **kwargs):
        raise httpx.ConnectError("no route to host")

    upstream(_boom)
    resp = await client.get("/api/geocode/?q=cebu")
    assert resp.status_code == 200, resp.text
    assert resp.json()["available"] is False


async def test_results_are_narrowed_to_what_a_cell_holds(client, upstream, no_wait):
    """Nominatim's response carries bounding boxes, OSM ids and licensing. None of
    it is what a location cell holds, so none of it becomes part of this API."""

    async def _answer(*args, **kwargs):
        return httpx.Response(
            200,
            json=[
                {
                    "display_name": "Cebu City, Cebu, Philippines",
                    "lat": "10.3157",
                    "lon": "123.8854",
                    "place_id": 12345,
                    "boundingbox": ["10.2", "10.4", "123.8", "123.9"],
                    "licence": "ODbL",
                },
                {"display_name": "broken row with no coordinates"},
            ],
            request=httpx.Request("GET", "http://example.test"),
        )

    upstream(_answer)
    resp = await client.get("/api/geocode/?q=cebu")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    # The unreadable row is one candidate lost, not a failed request.
    assert body["results"] == [
        {"label": "Cebu City, Cebu, Philippines", "lat": 10.3157, "lng": 123.8854}
    ]


async def test_an_answer_is_cached_so_a_team_asks_once(client, upstream, no_wait):
    calls = 0

    async def _answer(*args, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=[{"display_name": "Cebu City", "lat": "10.3", "lon": "123.9"}],
            request=httpx.Request("GET", "http://example.test"),
        )

    upstream(_answer)
    first = await client.get("/api/geocode/?q=cebu city")
    # Whitespace is collapsed and the key is case-folded, so the same question asked
    # slightly differently is still one call upstream.
    second = await client.get("/api/geocode/?q=Cebu   City")
    assert first.json() == second.json()
    assert calls == 1


async def test_an_empty_query_is_refused_by_the_route(client):
    resp = await client.get("/api/geocode/?q=")
    assert resp.status_code == 422, resp.text
