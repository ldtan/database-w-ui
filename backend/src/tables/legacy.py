"""`/api/lists/...` -> `/api/tables/...`, for one release only.

**Delete this module, its import in `main.py`, and `tests/test_legacy_list_routes.py`
after 2026-11-11.** It exists for a single deploy's worth of skew and nothing else.

`dbui-api` and `dbui-web` are separate Render services (`deploy/render/render.yaml`),
so the API moves before the bundle that calls it does. Between those two deploys a
browser holding the old bundle — an open tab, or a fresh load off a cached one — keeps
asking for `/api/lists/...`, and an API token script written before the rename does the
same. Without this they get 404s that look like their data is gone.

308 rather than 301/302: it is the only redirect that preserves the method *and* the
body, so a `POST /api/lists/{uuid}/items/` lands as a `POST` with its payload intact
rather than being replayed as a GET. The redirect is same-origin, so the
`Authorization` header survives it.

`include_in_schema=False` keeps the old spelling out of the OpenAPI document, which is
what `npm run gen:api` reads — so the generated client never learns these routes exist
and nothing new can start depending on them. No `Depends(get_current_user)` either:
this router only ever answers "look over there", and authenticating first would turn a
stale URL into a 401 that reads like a session problem instead of a moved one.
"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter(include_in_schema=False)

# `items` moved to `records` at the same time, so a legacy nested path needs both
# segments rewritten, not just the prefix.
_SEGMENTS = {"items": "records"}


@router.api_route(
    "/lists/{rest:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
)
async def moved(rest: str, request: Request) -> RedirectResponse:
    parts = [_SEGMENTS.get(part, part) for part in rest.split("/")]
    target = "/api/tables/" + "/".join(parts)
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(target, status_code=308)
