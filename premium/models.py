from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Optional

# Listings that look cheap for a reason. Matched against titles/descriptions.
_WARN_PATTERNS = {
    "bare ownership": r"nuda\s+propiedad|nuda\s+propietat",
    "occupied": r"(?<!des)\bocupad[ao]\b|\bokupa|sin\s+posesi[oó]n|ocupaci[oó]n\s+ilegal",
    "rented with tenants": r"alquilad[ao]\s+con\s+inquilin|con\s+inquilinos|llogat",
    "auction / debt transfer": r"cesi[oó]n\s+de\s+remate|cesi[oó]n\s+de\s+cr[eé]dito|subasta",
    "partial share": r"mitad\s+indivisa|pro\s+indiviso|\b\d{1,2}(?:[.,]\d+)?\s*%\s+(?:del?\s+)?(?:pleno\s+)?dominio"
                     r"|participaci[oó]n\s+indivisa",
}


def text_warnings(*texts: Optional[str]) -> list[str]:
    blob = " ".join(t for t in texts if t)
    return [w for w, pat in _WARN_PATTERNS.items() if re.search(pat, blob, re.I)]


@dataclass
class Listing:
    source: str                      # fotocasa | habitaclia | idealista | boe
    source_id: str
    url: str
    price: Optional[float]
    lat: Optional[float]
    lon: Optional[float]
    municipality: str
    title: str = ""
    rooms: Optional[int] = None
    bathrooms: Optional[int] = None
    surface_m2: Optional[float] = None
    floor: Optional[str] = None
    district: str = ""
    address: str = ""
    property_type: str = ""
    image: str = ""
    published: Optional[str] = None  # ISO date
    coords_approx: bool = False      # True when the pin is a zone/municipality centroid
    features: list[str] = field(default_factory=list)
    auction: Optional[dict[str, Any]] = None
    warnings: list[str] = field(default_factory=list)
    also_on: list[dict[str, str]] = field(default_factory=list)
    scores: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def price_m2(self) -> Optional[float]:
        if self.price and self.surface_m2:
            return round(self.price / self.surface_m2)
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        d["price_m2"] = self.price_m2
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Listing":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})
