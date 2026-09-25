"""Fotocasa: the search page embeds its results as JSON in <script id="__initial_props__">."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from ..http import PoliteSession
from ..models import Listing, text_warnings

log = logging.getLogger(__name__)

BASE = "https://www.fotocasa.es"
SEARCH = BASE + "/es/comprar/viviendas/{slug}/todas-las-zonas/l"
PAGE_SIZE = 30

SUBTYPES = {
    "Flat": "Piso", "Apartment": "Apartamento", "Penthouse": "Ático", "Duplex": "Dúplex",
    "GroundFloor": "Planta baja", "Loft": "Loft", "Study": "Estudio",
    "House_Chalet": "Casa", "Chalet": "Chalet", "SemidetachedHouse": "Casa pareada",
    "TerracedHouse": "Casa adosada", "Townhouse": "Casa adosada", "CountryHouse": "Casa rústica",
}


def extract_results(html: str) -> dict:
    tag = BeautifulSoup(html, "html.parser").find(id="__initial_props__")
    if not tag or not tag.string:
        raise ValueError("Fotocasa page has no __initial_props__ (layout changed or blocked)")
    return json.loads(tag.string)["initialSearch"]["result"]


def parse(r: dict, municipality_hint: str = "") -> Listing:
    feats = {f["key"]: f.get("value") for f in r.get("features", [])}
    addr = r.get("address") or {}
    coords = r.get("coordinates") or {}
    sub = r.get("buildingSubtype") or r.get("buildingType") or ""
    location = r.get("location") or addr.get("district") or ""
    ts = (r.get("dateOriginal") or r.get("date") or {}).get("timestamp")
    warnings = [w for flag, w in (("isBareOwnership", "bare ownership"), ("isOccupied", "occupied"),
                                  ("isRentedWithTenants", "rented with tenants"),
                                  ("isAuctioned", "auction / debt transfer")) if r.get(flag)]
    warnings += [w for w in text_warnings(r.get("description")) if w not in warnings]
    images = [m["src"] for m in r.get("multimedia", []) if m.get("type") == "image"]
    return Listing(
        source="fotocasa",
        source_id=str(r["id"]),
        url=BASE + (r.get("detail") or {}).get("es-ES", ""),
        price=r.get("rawPrice") or None,
        lat=coords.get("latitude"),
        lon=coords.get("longitude"),
        municipality=addr.get("municipality") or municipality_hint,
        title=f"{SUBTYPES.get(sub, sub or 'Vivienda')} en {location}".strip(),
        rooms=feats.get("rooms"),
        bathrooms=feats.get("bathrooms"),
        surface_m2=feats.get("surface"),
        floor=str(feats["floor"]) if feats.get("floor") is not None else None,
        district=addr.get("district") or "",
        address=location,
        property_type=sub,
        image=images[0] if images else "",
        published=datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat() if ts else None,
        # Fotocasa sets `accuracy` true when the pin is the exact address
        coords_approx=not r.get("accuracy", False),
        features=sorted(k for k in feats if k not in ("rooms", "bathrooms", "surface", "floor")),
        warnings=warnings,
    )


def fetch(cfg: dict, municipalities: list[dict], session: PoliteSession) -> Iterator[Listing]:
    q = cfg["search"]
    params = {"maxPrice": q["max_price"], "minRooms": q["min_rooms"], "minSurface": q["min_surface_m2"]}
    max_pages = cfg["sources"]["fotocasa"].get("max_pages_per_municipality", 150)
    for m in municipalities:
        seen: set[str] = set()
        page, pages = 1, 1
        while page <= min(pages, max_pages):
            url = SEARCH.format(slug=m["slug"]) + (f"/{page}" if page > 1 else "")
            res = extract_results(session.get(url, params=params).text)
            pages = max(1, math.ceil(res.get("count", 0) / PAGE_SIZE))
            new = 0
            for r in res.get("realEstates", []):
                if str(r["id"]) in seen or not r.get("rawPrice"):
                    continue
                seen.add(str(r["id"]))
                new += 1
                yield parse(r, m["name"])
            log.info("fotocasa %s p%d/%d: %d listings", m["name"], page, pages, new)
            if not new:
                break
            page += 1
