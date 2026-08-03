import csv
import io
import json
from itertools import islice
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.config import settings
from src.core.exceptions import ValidationError
from src.core.uploads import read_upload_text
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..dependencies import get_list_or_404
from ..models import List
from .schemas import ExportRequest, ImportSummary
from .services import export_rows, import_records

router = APIRouter(prefix="/{list_uuid}", tags=["transfer"])


@router.post("/export")
async def export_list(
    payload: ExportRequest,
    lst: List = Depends(get_list_or_404),
    current_user: User = Depends(get_current_user),
):
    # get_list_or_404 establishes 'view' on the list; the caller is threaded through
    # so the stream can also filter out records they cannot see individually.
    generator = export_rows(
        list_id=lst.id,
        user=current_user,
        fmt=payload.format,
        column_names=payload.columns,
        filters=payload.filters.model_dump(),
        sorts=[s.model_dump() for s in payload.sorts],
        limit=payload.limit,
    )
    media_type = "text/csv" if payload.format == "csv" else "application/json"
    filename = f"{lst.name}.{payload.format}"
    return StreamingResponse(
        generator,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _infer_format(filename: Optional[str], override: Optional[str]) -> str:
    # The file's own extension is authoritative — a wrong `format` field must not
    # cause the file to be parsed with the wrong parser. The override only
    # decides when the extension is missing/unrecognized.
    if filename:
        low = filename.lower()
        if low.endswith(".json"):
            return "json"
        if low.endswith(".csv"):
            return "csv"
    if override in ("csv", "json"):
        return override
    return "csv"


def _bounded(rows: Iterator[tuple], limit: int) -> list:
    """The file's rows as a list, or a refusal if there are more than `limit`.

    Materialised rather than streamed into the import, which is the point: rows
    are committed in batches, so a cap noticed part-way through would leave the
    list holding some of a file the caller was told was rejected. Reading one row
    past the limit is enough to know, and bounds what is held to the limit itself.
    """
    taken = list(islice(rows, limit + 1))
    if len(taken) > limit:
        raise ValidationError(
            f"This file has more than {limit} rows; split it and import each "
            "part separately. Nothing has been imported."
        )
    return taken


@router.post("/import", response_model=ImportSummary)
async def import_list(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    file: UploadFile = File(...),
    format: Optional[str] = Form(None),
):
    """Create records from an uploaded CSV or JSON file.

    Two file-level limits, both refusing before a single record exists:
    `IMPORT_MAX_BYTES` on the upload and `IMPORT_MAX_ROWS` on what is in it. A
    problem with one *row* is not one of these — it comes back inside the summary,
    because the rows around it are fine and are already saved.
    """
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.create
    )
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.edit
    )
    fmt = _infer_format(file.filename, format)
    try:
        text = await read_upload_text(file, settings.IMPORT_MAX_BYTES)
        if fmt == "json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid JSON file: {exc}",
                ) from exc
            elements = parsed if isinstance(parsed, list) else [parsed]
            # Number JSON rows by their 1-based position in the array.
            rows = ((i, element) for i, element in enumerate(elements, start=1))
        else:
            reader = csv.DictReader(io.StringIO(text))
            # reader.line_num is the actual source line of the row just read, so
            # reported rows stay accurate even when the file has blank lines.
            rows = ((reader.line_num, row) for row in reader)
        bounded = _bounded(rows, settings.IMPORT_MAX_ROWS)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return await import_records(db, lst, bounded, user=current_user)
