from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_superuser
from src.core.exceptions import ValidationError
from src.infrastructure.database import get_db

from ..models import User
from .parsing import parse_file
from .schemas import BatchUserSummary
from .services import import_users

router = APIRouter()


@router.post("/import", response_model=BatchUserSummary)
async def import_users_csv(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
    file: UploadFile = File(...),
):
    """Create many accounts from one CSV.

    Superuser-only, like every other account write -- accounts and group membership
    sit *underneath* the permission model, so whoever can mint them can route around
    it.

    Two kinds of failure, deliberately kept apart. A problem with the *file* -- wrong
    columns, too many rows, not text -- is a 400, because nothing in it can be
    trusted and nothing has been created. A problem with a *row* comes back inside
    the summary, because the rows around it were fine and are already saved.

    **The response carries generated passwords in plaintext.** It is the only place
    they exist; nothing stores them and there is no way to ask again. Do not log it.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file is not valid UTF-8 text; save it as UTF-8 CSV",
        ) from exc

    try:
        parsed = parse_file(text)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return await import_users(db, parsed)
