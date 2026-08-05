"""Reading an uploaded file without letting it decide how much memory to use.

Shared by the two upload endpoints — list records and user accounts — because the
hazard is the same in both and only one of them had noticed it.
"""
from typing import Optional, Protocol

from src.core.exceptions import ValidationError

# What an upload may weigh. Row caps bound the database work but not the memory: a
# file has to be read before anything can count its rows, and a JSON one is then
# parsed whole as well, which costs several times what it weighed on the wire. So a
# row cap on its own is not a bound at all — one enormous row passes it and still
# takes the process down. 8 MB is far above a thousand rows of anything either
# importer accepts, and small enough that the parsed form still fits on a 512 MB
# instance.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# How much is read at a time while checking that, so an oversized upload is refused
# one chunk past the limit instead of after the whole thing is in hand.
_UPLOAD_CHUNK = 64 * 1024


class Upload(Protocol):
    """The only part of an uploaded file this needs: bytes, on request.

    Typed structurally so nothing below the routers imports the web framework for one
    signature — the same reason `lists/columns/backfill.py` describes its `Deferrer`
    this way rather than naming Starlette's.
    """

    async def read(self, size: int = -1) -> bytes: ...


async def read_upload(file: Upload, limit: Optional[int] = None) -> bytes:
    """The upload's bytes, refusing anything past ``limit`` (default
    ``MAX_UPLOAD_BYTES``).

    Read in pieces rather than whole. `await file.read()` with no argument
    materialises whatever was sent before anything gets a chance to ask how big it
    is, which makes a size check pointless: by the time it could fail, the memory has
    already been spent.

    The default is resolved here rather than in the signature so the module global is
    read at call time — as a default argument it would be bound once at import and
    stop following the constant.
    """
    limit = MAX_UPLOAD_BYTES if limit is None else limit
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValidationError(
                f"This file is larger than {limit // (1024 * 1024)} MB; "
                "split it and import each part separately"
            )
        chunks.append(chunk)
    return b"".join(chunks)
