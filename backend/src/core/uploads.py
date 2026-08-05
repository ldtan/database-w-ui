"""Reading an uploaded file without letting it decide how much memory to use.

Shared by the two upload endpoints — list records and user accounts — because the
hazard is the same in both and only one of them had noticed it.
"""
from typing import Optional, Protocol

from src.core.config import settings
from src.core.exceptions import ValidationError

# The size limit itself is `settings.MAX_UPLOAD_BYTES`, because it belongs to the
# instance rather than to this function. What is worth stating here is why it has to
# exist at all: a row cap bounds the database work but not the memory, since a file
# has to be read before anything can count its rows, and a JSON one is then parsed
# whole again at several times its size on the wire. One enormous row passes a row
# cap untouched and still takes the process down.
#
# How much is read at a time while checking, so an oversized upload is refused one
# chunk past the limit instead of after the whole thing is in hand. Mechanism, not
# policy: no deployment has an opinion about it.
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
    ``settings.MAX_UPLOAD_BYTES``).

    Read in pieces rather than whole. `await file.read()` with no argument
    materialises whatever was sent before anything gets a chance to ask how big it
    is, which makes a size check pointless: by the time it could fail, the memory has
    already been spent.

    The default is resolved here rather than in the signature so the setting is read
    at call time — as a default argument it would be bound once at import and stop
    following it.
    """
    limit = settings.MAX_UPLOAD_BYTES if limit is None else limit
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
