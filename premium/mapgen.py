"""Renders output/map.html: one self-contained page with the data embedded, so it opens from disk."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .models import Listing

TEMPLATE = Path(__file__).parent / "templates" / "map.html"


def thumb(url: str) -> str:
    """Fotocasa/Habitaclia share an image CDN that serves a few fixed sizes."""
    if "static.fotocasa.es" in url:
        return re.sub(r"rule=[\w]+", "rule=web_328x246_ar", url) if "rule=" in url else url + "?rule=web_328x246_ar"
    return url


def _row(l: Listing) -> dict:
    s = l.scores
    c, d = s["components"], s["dist_km"]
    a = l.auction or None
    if a and a.get("id"):
        a = {k: a.get(k) for k in ("id", "state", "end", "value", "appraisal", "deposit", "claimed",
                                   "min_bid", "possession", "visitable", "habitual_residence",
                                   "year_built", "court")}
    return {
        "id": l.id, "src": l.source, "url": l.url, "t": l.title, "p": l.price, "r": l.rooms,
        "b": l.bathrooms, "s": l.surface_m2, "pm2": l.price_m2, "lat": round(l.lat, 5),
        "lon": round(l.lon, 5), "mu": l.municipality, "d": l.district, "a": l.address,
        "img": thumb(l.image), "pub": l.published, "approx": l.coords_approx, "fl": l.floor,
        "auc": a, "also": l.also_on, "warn": l.warnings,
        "c": [c["work"], c["sant_cugat"], c["castelldefels"], c["affordability"]],
        "km": [d["terrassa"], d["barcelona"], d["sant_cugat"], d["castelldefels"]],
    }


def render(listings: list[Listing], cfg: dict, fetched: dict[str, str], out: Path) -> None:
    payload = {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "fetched": fetched,
        "weights": cfg["weights"],
        "criteria": cfg["criteria"],
        "anchors": cfg["anchors"],
        "affordability": cfg["affordability"],
        "listings": [_row(l) for l in listings],
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    out.write_text(html, encoding="utf-8")
