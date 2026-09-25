"""Idealista via its official Search API (idealista.com blocks scrapers with a bot wall).

Get credentials at https://developers.idealista.com/access-request and export
IDEALISTA_API_KEY and IDEALISTA_API_SECRET. Without them this source is skipped.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Iterator
from urllib.parse import quote

from ..http import PoliteSession
from ..models import Listing, text_warnings

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.idealista.com/oauth/token"
SEARCH_URL = "https://api.idealista.com/3.5/es/search"


def credentials() -> tuple[str, str] | None:
    key, secret = os.getenv("IDEALISTA_API_KEY"), os.getenv("IDEALISTA_API_SECRET")
    return (key, secret) if key and secret else None


def token(session: PoliteSession, key: str, secret: str) -> str:
    basic = base64.b64encode(f"{quote(key)}:{quote(secret)}".encode()).decode()
    r = session.post(TOKEN_URL, data={"grant_type": "client_credentials", "scope": "read"},
                     headers={"Authorization": f"Basic {basic}"})
    r.raise_for_status()
    return r.json()["access_token"]


def parse(e: dict) -> Listing:
    return Listing(
        source="idealista",
        source_id=str(e["propertyCode"]),
        url=e.get("url", ""),
        price=e.get("price"),
        lat=e.get("latitude"),
        lon=e.get("longitude"),
        municipality=e.get("municipality", ""),
        title=(e.get("suggestedTexts") or {}).get("title")
              or f"{e.get('propertyType', 'Vivienda').capitalize()} en {e.get('address', '')}",
        rooms=e.get("rooms"),
        bathrooms=e.get("bathrooms"),
        surface_m2=e.get("size"),
        floor=e.get("floor"),
        district=e.get("district", ""),
        address=e.get("address", ""),
        property_type=e.get("propertyType", ""),
        image=e.get("thumbnail", ""),
        coords_approx=not e.get("showAddress", False),
        warnings=text_warnings(e.get("description")),
        features=[k for k in ("hasLift", "parkingSpace", "exterior", "hasSwimmingPool", "hasTerrace")
                  if e.get(k)],
    )


def fetch(cfg: dict, session: PoliteSession) -> Iterator[Listing]:
    creds = credentials()
    if not creds:
        log.warning("idealista: IDEALISTA_API_KEY / IDEALISTA_API_SECRET not set, skipping")
        return
    tok = token(session, *creds)
    q = cfg["search"]
    src = cfg["sources"]["idealista"]
    bedrooms = ",".join(str(n) for n in range(q["min_rooms"], 5))  # API caps at "4" meaning 4+
    for c in src["circles"]:
        page, pages = 1, 1
        while page <= min(pages, src.get("max_pages_per_circle", 10)):
            r = session.post(SEARCH_URL, headers={"Authorization": f"Bearer {tok}"}, data={
                "country": "es", "operation": "sale", "propertyType": "homes", "locale": "es",
                "center": f"{c['lat']},{c['lon']}", "distance": c["radius_m"],
                "maxPrice": q["max_price"], "minSize": q["min_surface_m2"], "bedrooms": bedrooms,
                "maxItems": 50, "numPage": page, "order": "publicationDate", "sort": "desc",
            })
            r.raise_for_status()
            j = r.json()
            pages = j.get("totalPages", 1)
            items = j.get("elementList", [])
            log.info("idealista %s p%d/%d: %d listings", c["label"], page, pages, len(items))
            yield from (parse(e) for e in items)
            page += 1
