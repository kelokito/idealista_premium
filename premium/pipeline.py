from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

import yaml

from . import mapgen
from .geo import Geocoder, Zone, haversine_km, norm_name
from .http import Blocked, from_config
from .models import Listing
from .scoring import score
from .sources import boe, fotocasa, habitaclia, idealista

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CACHE = DATA / "cache"
OUT = ROOT / "output"

SOURCES = ("fotocasa", "habitaclia", "boe", "idealista")
# When the same flat is on several portals, keep the richest record.
PRIORITY = {"idealista": 0, "fotocasa": 1, "habitaclia": 2, "boe": 3}


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# --- scraping -------------------------------------------------------------------

def scrape(cfg: dict, sources: Iterable[str], only: list[str] | None = None) -> None:
    zone = Zone(cfg["municipalities"])
    munis = zone.munis
    if only:
        wanted = {norm_name(o) for o in only}
        munis = [m for m in munis if norm_name(m["name"]) in wanted or m["slug"] in only]
    session = from_config(cfg)
    geocoder = Geocoder(CACHE / "geocode.json")
    RAW.mkdir(parents=True, exist_ok=True)

    for src in sources:
        if not cfg["sources"].get(src, {}).get("enabled", True):
            log.info("%s disabled in config", src)
            continue
        if src == "fotocasa":
            it = fotocasa.fetch(cfg, munis, session)
        elif src == "habitaclia":
            it = habitaclia.fetch(cfg, munis, session)
        elif src == "boe":
            it = boe.fetch(cfg, zone, session, geocoder, CACHE / "boe")
        elif src == "idealista":
            it = idealista.fetch(cfg, session)
        else:
            raise ValueError(f"unknown source {src}")

        got: list[Listing] = []
        try:
            for lst in it:
                got.append(lst)
        except Blocked as e:
            log.error("%s blocked us (%s). Keeping what we got; not retrying.", src, e)
        except Exception:
            log.exception("%s failed after %d listings", src, len(got))
        if not got:
            log.warning("%s: nothing fetched, previous data (if any) kept", src)
            continue
        path = RAW / f"{src}.json"
        # --only limits the portal scrapers to some municipalities: merge into the existing snapshot
        if only and src in ("fotocasa", "habitaclia") and path.exists():
            prev = {d["id"]: d for d in json.loads(path.read_text(encoding="utf-8"))["listings"]}
            prev.update({l.id: l.to_dict() for l in got})
            rows = list(prev.values())
        else:
            rows = [l.to_dict() for l in got]
        path.write_text(json.dumps({"fetched_at": datetime.now().isoformat(timespec="seconds"),
                                    "listings": rows}, ensure_ascii=False), encoding="utf-8")
        log.info("%s: saved %d listings -> %s", src, len(rows), path.relative_to(ROOT))


# --- building -------------------------------------------------------------------

def load_raw() -> tuple[list[Listing], dict[str, str]]:
    listings, fetched = [], {}
    for src in SOURCES:
        p = RAW / f"{src}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            fetched[src] = d.get("fetched_at", "")
            listings += [Listing.from_dict(x) for x in d["listings"]]
    return listings, fetched


def dedupe(listings: list[Listing]) -> list[Listing]:
    """Same price + rooms + surface in the same municipality within ~600 m = same flat.

    Also applies within one portal: agencies often post the same flat several times.
    """
    listings = sorted(listings, key=lambda l: (PRIORITY.get(l.source, 9), l.coords_approx))
    kept: list[Listing] = []
    buckets: dict[tuple, list[Listing]] = {}
    for l in listings:
        if l.source == "boe" or not l.surface_m2:
            kept.append(l)
            continue
        key = (round(l.price or 0), l.rooms, round(l.surface_m2), norm_name(l.municipality))
        dup = next((k for k in buckets.get(key, []) if _same_flat(k, l)), None)
        if dup:
            dup.also_on.append({"source": l.source, "url": l.url})
            if dup.coords_approx and not l.coords_approx:
                dup.lat, dup.lon, dup.coords_approx = l.lat, l.lon, False
            dup.floor = dup.floor or l.floor
            dup.image = dup.image or l.image
            dup.warnings += [w for w in l.warnings if w not in dup.warnings]
            continue
        buckets.setdefault(key, []).append(l)
        kept.append(l)
    return kept


def _same_flat(a: Listing, b: Listing) -> bool:
    if a.image and a.image.split("?")[0] == b.image.split("?")[0]:
        return True  # identical photo on the shared Fotocasa/Habitaclia CDN
    if haversine_km(a.lat, a.lon, b.lat, b.lon) >= 0.6:
        return False
    # Two units of one building can share price and size; within a portal, trust the floor.
    # (Across portals floors are too inconsistently written to rely on.)
    return not (a.source == b.source and a.floor and b.floor and a.floor != b.floor)


def build(cfg: dict) -> list[Listing]:
    zone = Zone(cfg["municipalities"])
    listings, fetched = load_raw()
    if not listings:
        raise SystemExit("No data yet. Run `python -m premium scrape` first.")
    inzone = [l for l in listings
              if l.lat is not None and l.lon is not None
              and zone.contains(l.municipality, l.lat, l.lon, l.district)]
    for l in inzone:
        l.municipality = zone.match(l.municipality)["name"]
    log.info("%d raw listings, %d inside the zone", len(listings), len(inzone))
    final = dedupe(inzone)
    log.info("%d after removing cross-portal duplicates", len(final))
    for l in final:
        l.scores = score(l, cfg)
    final.sort(key=lambda l: -l.scores["index"])

    OUT.mkdir(parents=True, exist_ok=True)
    rows = [l.to_dict() for l in final]
    (OUT / "listings.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(final, OUT / "listings.csv")
    mapgen.render(final, cfg, fetched, OUT / "map.html")
    n_ok = sum(l.scores["meets_criteria"] for l in final)
    log.info("%d listings meet the criteria. Map: %s", n_ok, (OUT / "map.html").relative_to(ROOT))
    return final


def write_csv(listings: list[Listing], path: Path) -> None:
    cols = ["index", "work_rating", "meets_criteria", "source", "price", "price_m2", "rooms",
            "bathrooms", "surface_m2", "municipality", "district", "address", "title",
            "km_terrassa", "km_barcelona", "km_sant_cugat", "km_castelldefels",
            "auction_end", "url", "also_on"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads accents
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for l in listings:
            s, d = l.scores, l.scores["dist_km"]
            w.writerow([s["index"], s["work_rating"], s["meets_criteria"], l.source, l.price,
                        l.price_m2, l.rooms, l.bathrooms, l.surface_m2, l.municipality, l.district,
                        l.address, l.title, d["terrassa"], d["barcelona"], d["sant_cugat"],
                        d["castelldefels"], (l.auction or {}).get("end"), l.url,
                        " ".join(a["url"] for a in l.also_on)])
