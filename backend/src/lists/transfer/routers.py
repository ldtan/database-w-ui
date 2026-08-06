import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ValidationError
from src.core.uploads import read_upload
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
        include_archived=payload.include_archived,
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


@router.post("/import", response_model=ImportSummary)
async def import_list(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    file: UploadFile = File(...),
    format: Optional[str] = Form(None),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.create
    )
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.edit
    )
    fmt = _infer_format(file.filename, format)
    # Size-checked while being read, not after: see `read_upload`.
    try:
        raw = await read_upload(file)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if fmt == "json":
        try:
            parsed = json.loads(raw.decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON file: {exc}",
            ) from exc
        elements = parsed if isinstance(parsed, list) else [parsed]
        # Number JSON rows by their 1-based position in the array.
        rows = ((i, element) for i, element in enumerate(elements, start=1))
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            # Answered the way the user batch import answers it, rather than as the
            # 500 an undecodable upload used to become.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The file is not valid UTF-8 text; save it as UTF-8 CSV",
            ) from exc
        reader = csv.DictReader(io.StringIO(text))
        # reader.line_num is the actual source line of the row just read, so
        # reported rows stay accurate even when the file has blank lines.
        rows = ((reader.line_num, row) for row in reader)

    # The row cap. It fires before anything is inserted, so a file over it leaves
    # the list exactly as it was.
    try:
        return await import_records(db, lst, rows, user=current_user)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
