import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database import get_db

from ..dependencies import get_list_or_404
from ..models import List
from .schemas import ExportRequest, ImportSummary
from .services import export_rows, import_records

router = APIRouter(prefix="/{list_uuid}", tags=["transfer"])


@router.post("/export")
async def export_list(
    payload: ExportRequest,
    lst: List = Depends(get_list_or_404),
):
    generator = export_rows(
        list_id=lst.id,
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
    if override in ("csv", "json"):
        return override
    if filename and filename.lower().endswith(".json"):
        return "json"
    return "csv"


@router.post("/import", response_model=ImportSummary)
async def import_list(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    format: Optional[str] = Form(None),
):
    fmt = _infer_format(file.filename, format)
    if fmt == "json":
        raw = await file.read()
        try:
            parsed = json.loads(raw.decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON file: {exc}",
            ) from exc
        rows = parsed if isinstance(parsed, list) else [parsed]
        # JSON element 1 maps to row 1 (no header line).
        summary = await import_records(db, lst, rows, row_offset=0)
    else:
        raw = await file.read()
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        # CSV data starts on line 2 (line 1 is the header).
        summary = await import_records(db, lst, reader, row_offset=1)
    return summary
