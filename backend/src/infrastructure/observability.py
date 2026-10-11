"""Logging, a request id, and the handler that stops a 500 being a silent one.

Before this the app had two loggers in the whole tree (`auth/seed.py` and
`columns/backfill.py`), no logging configuration, no request correlation and no
catch-all exception handler. A production 500 was uvicorn's default traceback in an
ephemeral log stream on a single instance: no route, no user, no id to match it to
the report that arrived an hour later. That is close to unactionable, and it is the
kind of gap that only shows up at the moment it is most expensive.

Three pieces, deliberately small:

- **`configure_logging`** — a JSON formatter, because Render's log viewer is a text
  box and grep over structured lines is what makes it usable. Plain text under DEBUG,
  where a person is reading them directly.
- **`RequestContextMiddleware`** — stamps each request with an id (honouring an
  inbound `X-Request-ID` so a proxy's id wins), echoes it on the response, and logs
  one line per request with method, path, status and duration. Raw ASGI rather than
  `BaseHTTPMiddleware`, which would put a task and a queue between the SSE
  generator and the socket for the life of every stream — see the class.
- **`install_exception_handler`** — logs the traceback with that id attached and
  answers a generic 500. The generic body is the point: the default handler's
  behaviour depends on the ASGI server, and an unhandled exception's `str()` is
  exactly the internal detail the import path was just fixed for leaking.

`request_id` is a `ContextVar`, so the log filter can reach it from anywhere in the
request without threading it through every call.
"""
import json
import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The id of the request being served, readable from any log call in it.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

logger = logging.getLogger("app.request")

# The host's liveness probe, which would otherwise be most of the log. The SSE
# stream is deliberately *not* here: it logs once when it closes, which is at most
# once per SSE_MAX_STREAM_SECONDS per viewer and is not enough to bury anything.
_QUIET_PATHS = ("/health",)


class _RequestIdFilter(logging.Filter):
    """Attach the current request id to every record, so any logger gets it."""

    def filter(self, record: logging.LogRecord) -> bool:
        # `getattr` first: a call site that passed one in `extra=` knows better than
        # the ContextVar — notably the 500 handler, which runs after this request's
        # var has been reset.
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        # Anything the call site attached with `extra=`, minus the noise every
        # LogRecord carries.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}


def configure_logging(debug: bool) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        logging.Formatter("%(levelname)s %(name)s [%(request_id)s] %(message)s")
        if debug
        else _JsonFormatter()
    )

    root = logging.getLogger()
    # Replace rather than append: `create_app()` runs once per test fixture in this
    # suite, and appending would give every line as many copies as apps built.
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # uvicorn installs its own handlers and propagates, which would double every
    # access line. Let them through this handler instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers[:] = []

    # Third-party DEBUG is not this app's debugging. Setting the root to DEBUG turns
    # `python_multipart` into a line per *field byte range* of every uploaded form
    # and `asyncio`/`httpx` into a running commentary — which buries the app's own
    # lines in normal use and made a single throttle test scroll off the screen.
    # Raised to WARNING regardless of the flag: a person turning DEBUG on wants to
    # see their own code, and can lower one of these by name if they truly need it.
    for name in ("python_multipart", "multipart", "asyncio", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class RequestContextMiddleware:
    """Raw ASGI rather than `BaseHTTPMiddleware`, because of the SSE route.

    `BaseHTTPMiddleware` runs the inner app in its own task and pipes the response
    body through a memory object stream. For an ordinary response that is invisible.
    For a **long-lived streaming** one it puts a task and a queue between the
    generator and the client for the life of the connection — and this app holds one
    of those open per viewer per table, for up to `SSE_MAX_STREAM_SECONDS`.

    To be accurate about what this is and is not: the version of this middleware
    built on `BaseHTTPMiddleware` was *suspected* of breaking
    `test_an_import_wakes_watchers_once_not_once_per_row` and was **not** the cause —
    that test was racing a one-second stream lifetime and failed with all middleware
    removed. This is written as raw ASGI anyway, on the general principle the events
    route already documents at length: nothing should come between that generator and
    the socket without a reason, and a request id needs none. It touches the messages
    and never the body — reads the id off the scope, appends a response header on
    `http.response.start`, and logs once the app has finished, which for a stream is
    when the tab closes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id")
        # A proxy's id wins, so one request has one id across every hop that logged
        # it. Only the generated half needs to be unguessable, and it need not be.
        rid = incoming or uuid.uuid4().hex[:16]
        # On the scope as well as in the ContextVar, because the two are readable
        # from different places. Starlette's `ServerErrorMiddleware` — which runs the
        # handler below on an unhandled exception — sits *outside* this one, so by
        # the time it is reached the `finally` here has already reset the var.
        # `request.state` is backed by `scope["state"]` and survives, which is what
        # lets the 500 carry the same id as the access line for the request that
        # produced it. Without it, the one response where the id matters most is the
        # one response that has none.
        scope.setdefault("state", {})["request_id"] = rid

        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = 500  # if the app raises, that is what the caller is about to get

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", rid.encode())
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = scope.get("path", "")
            if not path.endswith(_QUIET_PATHS):
                logger.info(
                    "%s %s %s",
                    scope.get("method", "-"),
                    path,
                    status,
                    extra={
                        "method": scope.get("method", "-"),
                        "path": path,
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                )
            # After the access line, not before it: reset first and the one log entry
            # guaranteed to have an id is the one that answers "-".
            request_id_var.reset(token)


def install_exception_handler(app) -> None:
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", request_id_var.get())
        # `logger.exception` is the whole point: this is the only place the traceback
        # of an unexpected failure is written down.
        logger.exception(
            "unhandled exception on %s %s",
            request.method,
            request.url.path,
            extra={"path": request.url.path, "method": request.method, "request_id": rid},
        )
        return JSONResponse(
            status_code=500,
            # Deliberately says nothing about the exception — an unhandled error's
            # `str()` is exactly the internal detail the import path was fixed for
            # leaking. The id is what turns a user's report into the log line.
            content={"detail": "Internal server error", "request_id": rid},
            headers={"X-Request-ID": rid},
        )
