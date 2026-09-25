"""Habitaclia: results are embedded as `window.__INITIAL_PROPS__ = JSON.parse("...")`."""
from __future__ import annotations

import json
import logging
import re
from typing import Iterator

from ..http import PoliteSession
from ..models import Listing, text_warnings

log = logging.getLogger(__name__)

BASE = "https://www.habitaclia.com"
SEARCH = BASE + "/comprar/viviendas/barcelona-provincia/{slug}/s"

_PROPS = re.compile(r'window\.__INITIAL_PROPS__\s*=\s*JSON\.parse\((".*?")\);?\s*</script>', re.S)

SUBTYPES = {
    "flat": "Piso", "penthouse": "Ático", "duplex": "Dúplex", "groundFloor": "Planta baja",
    "apartment": "Apartamento", "loft": "Loft", "studio": "Estudio", "house": "Casa",
    "chalet": "Chalet", "semiDetachedHouse": "Casa pareada", "terracedHouse": "Casa adosada",
}


def extract_context(html: str) -> dict:
    m = _PROPS.search(html)
    if not m:
        raise ValueError("Habitaclia page has no __INITIAL_PROPS__ (layout changed or blocked)")
    return json.loads(json.loads(m.group(1)))["initialSearchResultsPage"]["initialSearchContext"]


def parse(it: dict, municipality_hint: str = "") -> Listing:
    summ = it.get("summary") or {}
    loc = summ.get("location") or {}
    coords = loc.get("coordinates") or {}
    prop = it.get("property") or {}
    price = ((it.get("transaction") or {}).get("price") or {})
    addr = loc.get("address") or {}
    street = " ".join(x for x in (addr.get("streetName"), addr.get("streetNumber")) if x)
    images = [i["url"] for i in (summ.get("multimedia") or {}).get("images", []) if i.get("url")]
    sub = prop.get("propertySubtype") or prop.get("propertyType") or ""
    return Listing(
        source="habitaclia",
        source_id=str(it.get("legacyNumericId") or it["id"]),
        url=BASE + ((it.get("urls") or {}).get("canonical") or it.get("navigationUrl", "")),
        price=None if price.get("hidden") else price.get("amount"),
        lat=coords.get("latitude"),
        lon=coords.get("longitude"),
        municipality=loc.get("municipality") or municipality_hint,
        title=summ.get("title") or f"{SUBTYPES.get(sub, 'Vivienda')} en {loc.get('district', '')}",
        rooms=prop.get("rooms"),
        bathrooms=prop.get("bathrooms"),
        surface_m2=prop.get("builtSurface"),
        floor=str(prop["floor"]) if prop.get("floor") is not None else None,
        district=loc.get("district") or "",
        address=loc.get("displayAddressLine") or street or loc.get("displayZoneLine") or "",
        property_type=SUBTYPES.get(sub, sub),
        image=images[0] if images else "",
        coords_approx=loc.get("visibility") not in ("EXACT", "STREET"),
        warnings=text_warnings(summ.get("title"), summ.get("description")),
        features=[f.lower() for f in ((prop.get("features") or {}).get("has") or [])],
    )


def fetch(cfg: dict, municipalities: list[dict], session: PoliteSession) -> Iterator[Listing]:
    q = cfg["search"]
    params = {"maxPrice": q["max_price"], "minRooms": q["min_rooms"], "minSurface": q["min_surface_m2"]}
    max_pages = cfg["sources"]["habitaclia"].get("max_pages_per_municipality", 150)
    for m in municipalities:
        seen: set[str] = set()
        page, pages = 1, 1
        while page <= min(pages, max_pages):
            url = SEARCH.format(slug=m["slug"]) + (f"/{page}" if page > 1 else "")
            ctx = extract_context(session.get(url, params=params).text)
            if ctx.get("geography", {}).get("layer") not in ("municipality", None):
                log.warning("habitaclia slug %r resolved to %s, skipping", m["slug"], ctx["geography"])
                break
            results = ctx.get("results") or {}
            pages = (results.get("pagination") or {}).get("totalPages", 1)
            new = 0
            for it in results.get("items", []):
                lst = parse(it, m["name"])
                if lst.source_id in seen or not lst.price:
                    continue
                seen.add(lst.source_id)
                new += 1
                yield lst
            log.info("habitaclia %s p%d/%d: %d listings", m["name"], page, pages, new)
            if not new:
                break
            page += 1
