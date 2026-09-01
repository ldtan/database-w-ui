"""A ceiling on the request body, for every route rather than the upload ones.

`core/uploads.py` caps a *file* at `MAX_UPLOAD_BYTES` while reading it, which covers
import and the batch user CSV. Nothing capped a JSON body: `ListItemCreate.data` is a
`dict` to Pydantic, `ColumnConfig` forbids unknown keys but bounds none of the values
it does take, and Starlette imposes no limit of its own. So

    POST /lists/{u}/items/  {"data": {"<text column uuid>": "A" * 50_000_000}}

was buffered, parsed, sanitised and stored — on a 512 MB instance, into a column whose
`max_length` is unset by default. The cell limit is a separate and narrower question
(a `text` column can legitimately hold a long document); this is the blunt one that
stops a single request costing the process its memory.

Two checks, because either alone has a hole:

- **`Content-Length`**, refused before the body is read at all. This is the cheap one
  and catches every ordinary client.
- **The stream**, counted as it arrives, because a chunked request has no
  `Content-Length` to check and would otherwise walk straight past the first.

Answers **413**, which is the status for exactly this and which a client can act on,
rather than letting the request die somewhere less legible.
"""
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Raw ASGI rather than BaseHTTPMiddleware: this has to wrap `receive` to count the
# body as it streams, and BaseHTTPMiddleware does not hand that over.
class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._refuse(scope, receive, send)
                    return
            except ValueError:
                # A malformed header is not this middleware's business to reject —
                # the server below will answer for it. Fall through to the counter,
                # which is what would have caught the body anyway.
                pass

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raising here would surface as a 500. Truncating the stream and
                    # letting the route fail on a short body would be worse still, so
                    # the flag is checked by the wrapper below instead.
                    raise _TooLarge()
            return message

        try:
            await self.app(scope, counting_receive, send)
        except _TooLarge:
            await self._refuse(scope, receive, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        mb = self.max_bytes / (1024 * 1024)
        response = JSONResponse(
            status_code=413,
            content={"detail": f"Request body is too large (limit {mb:.0f} MB)."},
        )
        await response(scope, receive, send)


class _TooLarge(Exception):
    """Internal signal from the receive wrapper; never leaves this module."""
