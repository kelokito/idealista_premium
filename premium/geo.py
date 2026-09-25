from __future__ import annotations

import json
import logging
import math
import re
import time
import unicodedata
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

EARTH_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


_ARTICLES = re.compile(r"^(l|el|la|els|les)\s+")


def norm_name(name: str) -> str:
    """'L'Hospitalet de Llobregat' / 'Hospitalet de Llobregat (L')' -> 'hospitalet de llobregat'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z]+", " ", s).strip()
    s = _ARTICLES.sub("", s)
    return re.sub(r"\s+", " ", s)


class Zone:
    """Decides whether a listing falls in the configured area."""

    def __init__(self, municipalities: list[dict]):
        self.munis = [m for m in municipalities if m.get("enabled", True)]
        self.by_name = {norm_name(m["name"]): m for m in self.munis}
        # Barcelona city is often reported as "Barcelona Capital"
        if "barcelona" in self.by_name:
            self.by_name["barcelona capital"] = self.by_name["barcelona"]

    def match(self, municipality: str) -> Optional[dict]:
        return self.by_name.get(norm_name(municipality))

    def contains(self, municipality: str, lat: Optional[float], lon: Optional[float],
                 district: str = "") -> bool:
        m = self.match(municipality)
        if not m:
            return False
        r = m.get("restrict")
        if not r:
            return True
        if r.get("district_keyword") and r["district_keyword"] in norm_name(district):
            return True
        if lat is None or lon is None:
            return False
        return haversine_km(lat, lon, r["lat"], r["lon"]) <= r["radius_km"]


class Geocoder:
    """Nominatim (OpenStreetMap) with an on-disk cache and the required 1 req/s limit."""

    URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: dict[str, Optional[list[float]]] = {}
        if cache_path.exists():
            self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "idealista-premium/0.1 (personal home search)"

    def geocode(self, query: str) -> Optional[tuple[float, float]]:
        if query in self.cache:
            hit = self.cache[query]
            return tuple(hit) if hit else None
        wait = 1.1 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.s.get(self.URL, params={"q": query, "format": "json", "limit": 1,
                                             "countrycodes": "es"}, timeout=20)
            self._last = time.monotonic()
            res = r.json() if r.ok else []
        except (requests.RequestException, ValueError) as e:
            log.warning("geocode failed for %r: %s", query, e)
            return None
        hit = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
        self.cache[query] = hit
        self.save()
        return tuple(hit) if hit else None

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0), encoding="utf-8")
