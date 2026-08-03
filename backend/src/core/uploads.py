"""Reading an uploaded file without trusting how big it is.

Every `/import` endpoint used to call `await file.read()`, which is the whole
upload in memory in one object. Starlette spools the multipart body to a temp
file past a megabyte, so the parse itself survives a large POST — but that one
`read()` pulls it all back, and on a 512 MB instance a single file is enough to
take the process down. Nothing in front of the app stops it: there is no proxy
body limit in the Render blueprint, and `Content-Length` is a claim by the
caller, absent entirely under chunked encoding.

So the cap is counted against bytes actually read, in chunks, and the read stops
the moment it is passed. Refusing costs one chunk of memory however big the file
was.
"""
from fastapi import UploadFile

from .exceptions import ValidationError

# Read granularity. Big enough that a normal import is a handful of reads, small
# enough that overshooting the cap by one chunk is not worth thinking about.
_CHUNK = 64 * 1024


def _describe(limit: int) -> str:
    mb = limit / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 1 else f"{limit / 1024:.0f} KB"


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """The file's bytes, or `ValidationError` if it is larger than `max_bytes`.

    Reads one chunk past the limit and stops there, deliberately: the exact size
    of a file that is too big does not change the answer, and measuring it would
    mean reading all of it, which is the thing being avoided.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValidationError(
                f"This file is larger than {_describe(max_bytes)}. Split it and "
                "upload each part separately."
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def read_upload_text(file: UploadFile, max_bytes: int) -> str:
    """`read_upload`, decoded as UTF-8.

    `utf-8-sig` because a spreadsheet saved as CSV from Excel starts with a byte
    order mark, and a stray BOM would otherwise become part of the first column's
    name and match nothing.
    """
    raw = await read_upload(file, max_bytes)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            "The file is not valid UTF-8 text; save it as UTF-8 and try again"
        ) from exc
