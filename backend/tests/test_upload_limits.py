"""The size cap on an uploaded file, and that it is paid for in chunks.

`src/core/uploads.py` exists for one reason: `await file.read()` on an
`UploadFile` is the whole upload in memory, and the size of that upload is
decided by whoever is posting. These tests are mostly about *how* the refusal
happens rather than that it happens — a cap that reads the file to the end
before saying no has not helped.
"""
import io

import pytest
from fastapi import UploadFile

from src.core.exceptions import ValidationError
from src.core.uploads import _CHUNK, read_upload, read_upload_text


class _CountingBytes(io.BytesIO):
    """A BytesIO that remembers how much of itself was handed out."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.given = 0

    def read(self, size=-1):  # noqa: D102 - see the class docstring
        chunk = super().read(size)
        self.given += len(chunk)
        return chunk


def _upload(data: bytes) -> tuple[UploadFile, _CountingBytes]:
    source = _CountingBytes(data)
    return UploadFile(file=source, filename="rows.csv"), source


async def test_a_file_under_the_cap_comes_back_whole():
    upload, _ = _upload(b"name,age\nAnn,30\n")
    assert await read_upload(upload, 1024) == b"name,age\nAnn,30\n"


async def test_a_file_at_exactly_the_cap_is_allowed():
    """The limit is a maximum, not a threshold to stay under."""
    upload, _ = _upload(b"x" * 100)
    assert len(await read_upload(upload, 100)) == 100


async def test_an_oversize_file_is_refused_without_being_read_to_the_end():
    """The point of the whole module.

    A hundred chunks are posted against a cap of one. What matters is not the
    error but `given`: the read stops one chunk past the limit, so refusing costs
    a fixed amount of memory however big the thing being refused was.
    """
    upload, source = _upload(b"x" * (_CHUNK * 100))

    with pytest.raises(ValidationError, match="larger than"):
        await read_upload(upload, _CHUNK)

    assert source.given <= _CHUNK * 2


async def test_a_byte_order_mark_is_not_part_of_the_first_heading():
    """What a spreadsheet saved as "CSV UTF-8" actually writes.

    Decoded as plain utf-8 the mark survives into the first key, so the header
    reads `﻿name` and matches no column — an import where every row is
    accepted and the first column is silently empty.
    """
    upload, _ = _upload("﻿name,age\nAnn,30\n".encode("utf-8"))
    assert (await read_upload_text(upload, 1024)).startswith("name,age")


async def test_a_file_that_is_not_text_is_refused_as_a_file_problem():
    upload, _ = _upload(b"\xff\xfe\x00binary")
    with pytest.raises(ValidationError, match="UTF-8"):
        await read_upload_text(upload, 1024)
