from typing import Optional

from pydantic import BaseModel


class GeocodeResult(BaseModel):
    """One candidate for a typed address.

    Deliberately three fields and no more. Nominatim's response carries a great deal
    besides — bounding boxes, OSM ids, an address broken into components, licensing
    — and none of it is what a `location` cell holds, so none of it is passed on.
    Narrowing here rather than in the browser is what keeps the upstream's response
    shape from becoming part of this app's API.
    """

    label: str
    lat: float
    lng: float


class GeocodeResponse(BaseModel):
    results: list[GeocodeResult]
    # False when the search could not be run at all — no geocoder is configured, or
    # the upstream did not answer. Distinct from an empty `results`, which means the
    # geocoder answered and knows nothing by that name; the picker says so rather
    # than showing "no matches" for an outage.
    available: bool = True
    detail: Optional[str] = None
