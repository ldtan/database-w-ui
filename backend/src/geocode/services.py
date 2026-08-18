"""Turn a typed address into coordinates, through a geocoder we do not own.

**Why this is a server route rather than a browser fetch.** Nominatim's usage policy
asks for an identifiable User-Agent and a bounded request rate, and a browser can
set neither — it sends its own User-Agent and there is no way to coordinate a rate
limit across every open tab. Routing it here buys three things: one identity the
operator can be contacted through, one rate limit for the whole deployment, and a
cache, so twenty people typing the same office address ask once.

It is also what keeps the upstream swappable. The URL is configuration, the response
is narrowed to `GeocodeResult` before it leaves this module, and nothing outside
knows which geocoder answered — so a deployment can point at its own Nominatim, or
at nothing at all, without the picker changing.

**Failure is never an error here.** A location column works without geocoding: an
address can be typed and a pin dropped by hand. So an outage, a timeout, a missing
configuration and a rate-limit rejection all come back as `available: false` with a
sentence, and the picker stays usable. Turning an unreachable third party into a 502
would make somebody's address field stop working because a volunteer-run service
was busy.
"""
import asyncio
import time
from collections import OrderedDict
from typing import Optional

import httpx

from src.core.config import settings

from .schemas import GeocodeResponse, GeocodeResult

# Queries whose answers are held. An address does not move, so a hit is as good as a
# fresh call and costs the upstream nothing. Bounded because this is process memory
# on a 512 MB instance, and evicted oldest-first.
_CACHE_MAX = 500
# How long a cached answer is trusted. Long, because places do not move; not
# unbounded, because a geocoder that learns a new address should eventually be asked
# again by a deployment that has been up for a month.
_CACHE_TTL_SECONDS = 24 * 60 * 60

_cache: "OrderedDict[str, tuple[float, list[GeocodeResult]]]" = OrderedDict()
# One request at a time, deployment-wide. Nominatim's policy is one call a second,
# and a semaphore plus the minimum spacing below is how a burst of typing becomes a
# queue rather than a ban. The lock is per process, which is the same
# single-instance assumption the login throttle already documents.
_gate = asyncio.Semaphore(1)
_last_call = 0.0


def _spacing() -> float:
    return max(0.0, float(settings.GEOCODE_MIN_INTERVAL_SECONDS))


def _cache_get(key: str) -> Optional[list[GeocodeResult]]:
    hit = _cache.get(key)
    if hit is None:
        return None
    stored_at, results = hit
    if time.monotonic() - stored_at > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return results


def _cache_put(key: str, results: list[GeocodeResult]) -> None:
    _cache[key] = (time.monotonic(), results)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _unavailable(detail: str) -> GeocodeResponse:
    return GeocodeResponse(results=[], available=False, detail=detail)


async def search(q: str, limit: int = 5) -> GeocodeResponse:
    """Candidates for a typed address, or an explanation of why there are none."""
    query = " ".join(q.split())  # collapse whitespace so the cache key is stable
    if not query:
        return GeocodeResponse(results=[])
    if not settings.GEOCODE_URL:
        return _unavailable(
            "Address search is not configured. You can still type an address and "
            "place the pin yourself."
        )

    key = f"{query.casefold()}|{limit}"
    cached = _cache_get(key)
    if cached is not None:
        return GeocodeResponse(results=cached)

    global _last_call
    async with _gate:
        # Inside the gate, so the wait is what spaces calls out rather than what
        # every caller pays: a queued request sleeps only for whatever is left of
        # the interval when its turn comes.
        wait = _spacing() - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=settings.GEOCODE_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    settings.GEOCODE_URL,
                    params={"q": query, "format": "jsonv2", "limit": limit},
                    headers={"User-Agent": settings.GEOCODE_USER_AGENT},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError):
            # Includes a timeout, a refused connection, an upstream 4xx/5xx and a
            # body that is not JSON. All the same thing to the caller: no answer
            # this time, type the address yourself.
            return _unavailable("Address search is unavailable right now.")
        finally:
            _last_call = time.monotonic()

    results = []
    for row in payload if isinstance(payload, list) else []:
        try:
            results.append(
                GeocodeResult(
                    label=str(row["display_name"]),
                    lat=float(row["lat"]),
                    lng=float(row["lon"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # a row we cannot read is one candidate lost, not a failure

    _cache_put(key, results)
    return GeocodeResponse(results=results)
