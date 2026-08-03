from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_superuser
from src.core.config import settings
from src.core.exceptions import ValidationError
from src.core.uploads import read_upload_text
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

    Two kinds of failure, deliberately kept apart. A problem with the *file* -- too
    large, wrong columns, too many rows, not text -- is a 400, because nothing in it
    can be trusted and nothing has been created. A problem with a *row* comes back
    inside the summary, because the rows around it were fine and are already saved.

    The size limit is `IMPORT_MAX_BYTES`, shared with record import; the row limit is
    `parsing.MAX_ROWS`, which is its own and much lower, because a row here mints a
    password and argon2 is deliberately expensive.

    **The response carries generated passwords in plaintext.** It is the only place
    they exist; nothing stores them and there is no way to ask again. Do not log it.
    """
    try:
        text = await read_upload_text(file, settings.IMPORT_MAX_BYTES)
        parsed = parse_file(text)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return await import_users(db, parsed)
