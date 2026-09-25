"""Criteria gate + the 0-100 index.

Components (each 0..1):
  work           balance between Terrassa centre and Barcelona
  sant_cugat     closeness to Sant Cugat
  castelldefels  closeness to Castelldefels
  affordability  200k -> 1.0 ... 400k -> 0.0

The same formulas are mirrored in the map's JavaScript so weights can be tuned live;
keep premium/templates/map.html in sync if you change them.
"""
from __future__ import annotations

from typing import Optional

from .geo import haversine_km
from .models import Listing


def proximity(d_km: float, full_km: float, zero_km: float) -> float:
    if d_km <= full_km:
        return 1.0
    if d_km >= zero_km:
        return 0.0
    return 1.0 - (d_km - full_km) / (zero_km - full_km)


def work_score(s_terrassa: float, s_barcelona: float, balance_weight: float) -> float:
    mean = (s_terrassa + s_barcelona) / 2
    worst = min(s_terrassa, s_barcelona)
    return (1 - balance_weight) * mean + balance_weight * worst


def affordability(price: Optional[float], best: float, worst: float) -> Optional[float]:
    if not price:
        return None
    if price <= best:
        return 1.0
    if price >= worst:
        return 0.0
    return 1.0 - (price - best) / (worst - best)


def meets_criteria(l: Listing, crit: dict) -> bool:
    """Unknown rooms/surface (common in auctions) don't disqualify; unknown price does."""
    if not l.price or l.price > crit["max_price"]:
        return False
    if l.rooms is not None and l.rooms < crit["min_rooms"]:
        return False
    if l.surface_m2 is not None and l.surface_m2 < crit["min_surface_m2"]:
        return False
    return True


def score(l: Listing, cfg: dict) -> dict:
    a = cfg["anchors"]
    p = cfg["proximity"]
    dist = {k: round(haversine_km(l.lat, l.lon, v["lat"], v["lon"]), 2) for k, v in a.items()}
    prox = {k: proximity(d, p["full_km"], p["zero_km"]) for k, d in dist.items()}
    comp = {
        "work": work_score(prox["terrassa"], prox["barcelona"], cfg["work"]["balance_weight"]),
        "sant_cugat": prox["sant_cugat"],
        "castelldefels": prox["castelldefels"],
        "affordability": affordability(l.price, cfg["affordability"]["best_price"],
                                       cfg["affordability"]["worst_price"]),
    }
    w = cfg["weights"]
    total_w = sum(w.values())
    index = sum(w[k] * (comp[k] or 0.0) for k in w) / total_w * 100
    return {
        "dist_km": dist,
        "components": {k: round(v, 3) if v is not None else None for k, v in comp.items()},
        "work_rating": round(comp["work"] * 10, 1),   # the Terrassa/Barcelona balance rating, 0-10
        "index": round(index, 1),
        "meets_criteria": meets_criteria(l, cfg["criteria"]),
    }
