"""Rate limiting for the login endpoint.

Login is the one route that has to answer before the caller has proved anything,
so it is the only place an attacker can guess at. Without a limit here a public
URL offers unlimited credential guessing against a username that was never a
secret — `admin` is the seeded account and it is named in
deploy/render/render.yaml — and every attempt spends an argon2 verification,
deliberately expensive, on the single instance the free tier runs. The same
missing limit is both the brute-force hole and the cheapest way to take the app
down, which is why the check below runs *before* the password is verified.

The counters live in this process, not in the database or in Redis. That is
exact on the current deployment, which runs exactly one instance, and becomes
per-instance — so N times looser — the moment the service is scaled. It belongs
on the same list as the `alembic upgrade` note in the blueprint: things that are
correct only because there is one instance.

Every function here is plain and synchronous. Under a single event loop that
makes each one atomic with respect to the others — there is no await inside for
another request to interleave at — so the shared dict needs no lock.
"""
from collections import deque
from math import ceil
from time import monotonic

from fastapi import Request

from src.core.config import settings


# key -> monotonic timestamps of recent failures, oldest first. A bucket is
# trimmed to the window whenever it is read, so it never holds more entries than
# its own threshold.
_failures: dict[tuple[str, str], deque[float]] = {}

# Above this many tracked keys, _sweep starts evicting quiet buckets. A spray
# across thousands of usernames would otherwise grow this dict without bound.
_MAX_TRACKED_KEYS = 4096


def _window_seconds() -> float:
    return settings.LOGIN_THROTTLE_WINDOW_MINUTES * 60


def _user_key(username: str) -> tuple[str, str]:
    # Case-folded so that alternating capitals cannot buy a fresh budget per
    # spelling. This only ever makes the limit stricter, so it holds whether or
    # not the account lookup itself is case-sensitive.
    return ("user", username.strip().lower())


def _ip_key(ip: str) -> tuple[str, str]:
    return ("ip", ip)


def client_ip(request: Request) -> str:
    """The caller's address as well as it can be known from behind a proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # The rightmost entry is the peer our own proxy received the connection
        # from. Everything to the left of it is whatever the caller chose to
        # send and is theirs to invent — trusting that would hand an attacker a
        # fresh bucket per request. This assumes exactly one trusted hop in
        # front of the app, which is the deployment in render.yaml.
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"


def _wait_for(key: tuple[str, str], limit: int, now: float) -> float | None:
    """Seconds until `key` is under `limit` again, or None if it already is."""
    if limit <= 0:  # 0 disables this bucket entirely.
        return None
    bucket = _failures.get(key)
    if bucket is None:
        return None

    cutoff = now - _window_seconds()
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()

    if len(bucket) < limit:
        return None
    # The budget reopens when the oldest failure still being counted ages out.
    return bucket[0] + _window_seconds() - now


def retry_after_seconds(username: str, ip: str) -> int | None:
    """How long this attempt must wait, or None to let it through."""
    now = monotonic()
    waits = [
        wait
        for wait in (
            _wait_for(
                _user_key(username), settings.LOGIN_MAX_FAILURES_PER_USERNAME, now
            ),
            _wait_for(_ip_key(ip), settings.LOGIN_MAX_FAILURES_PER_IP, now),
        )
        if wait is not None
    ]
    if not waits:
        return None
    # Never advertise 0: a client obeying `Retry-After: 0` would come straight
    # back and be turned away again.
    return max(1, ceil(max(waits)))


def record_failure(username: str, ip: str) -> None:
    """Count one wrong password against both the username and the address."""
    now = monotonic()
    _sweep(now)
    for key in (_user_key(username), _ip_key(ip)):
        _failures.setdefault(key, deque()).append(now)


def clear_failures(username: str) -> None:
    """Forget a username's failures after it is signed in successfully.

    The address bucket is deliberately left alone. Clearing it too would make a
    single valid account of no value a reset button, to be pressed between runs
    of guesses at every other account from the same address.
    """
    _failures.pop(_user_key(username), None)


def _sweep(now: float) -> None:
    """Evict buckets that have gone quiet. Only walks the dict once it is big
    enough to be worth walking — the common case is a handful of keys."""
    if len(_failures) <= _MAX_TRACKED_KEYS:
        return
    cutoff = now - _window_seconds()
    for key, bucket in list(_failures.items()):
        if not bucket or bucket[-1] <= cutoff:
            del _failures[key]


def reset() -> None:
    """Drop every counter. For tests, which share one process and would
    otherwise leak a locked-out username from one case into the next."""
    _failures.clear()
