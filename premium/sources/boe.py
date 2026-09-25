"""BOE public auctions (subastas.boe.es): housing (tipo I / subtipo 501) in Barcelona province.

Flow: advanced search -> per auction, the "Bienes" tab (address, cadastral ref) -> keep those in
the zone -> "Información general" tab (auction value, deposit, dates) -> Catastro for the exact
coordinates and built surface. Detail pages are cached on disk; the search runs every time
so auction state stays fresh.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from ..geo import Geocoder, Zone
from ..http import PoliteSession
from ..models import Listing, text_warnings

log = logging.getLogger(__name__)

BASE = "https://subastas.boe.es"
SEARCH = BASE + "/subastas_ava.php"
DETAIL = BASE + "/detalleSubasta.php"
CATASTRO_COORDS = ("https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/"
                   "COVCCoordenadas.svc/json/Consulta_CPMRC")
CATASTRO_DNP = ("https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/"
                "COVCCallejero.svc/json/Consulta_DNPRC")

STATE_LABEL = {"PU": "Próxima apertura", "EJ": "Celebrándose"}

_NUMWORDS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
             "siete": 7, "quatre": 4, "cinc": 5, "sis": 6}
_ROOMS = re.compile(
    r"\b(\d{1,2}|un[oa]?|dos|tres|cuatro|quatre|cinco|cinc|seis|sis|siete)\s+"
    r"(?:dormitorios?|habitaciones|habitacions|hab\.|cuartos|dormitoris)", re.I)
_SURFACE = re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s*(?:m2|m²|metros\s+cuadrados|m\.?\s*c\.?|metres)", re.I)


def search_form(state: str) -> dict:
    return {
        "campo[0]": "SUBASTA.ORIGEN", "dato[0]": "",
        "campo[1]": "SUBASTA.AUTORIDAD", "dato[1]": "",
        "campo[2]": "SUBASTA.ESTADO.CODIGO", "dato[2]": state,
        "campo[3]": "BIEN.TIPO", "dato[3]": "I",
        "dato[4]": "501",  # vivienda
        "campo[5]": "BIEN.DIRECCION", "dato[5]": "",
        "campo[6]": "BIEN.CODPOSTAL", "dato[6]": "",
        "campo[7]": "BIEN.LOCALIDAD", "dato[7]": "",
        "campo[8]": "BIEN.COD_PROVINCIA", "dato[8]": "08",
        "page_hits": "500",
        "sort_field[0]": "SUBASTA.FECHA_FIN", "sort_order[0]": "asc",
        "accion": "Buscar",
    }


def parse_search(html: str) -> list[dict]:
    out = []
    for li in BeautifulSoup(html, "html.parser").select("li.resultado-busqueda"):
        h3 = li.find("h3")
        m = re.search(r"(SUB-[\w-]+)", h3.get_text() if h3 else "")
        if not m:
            continue
        ps = [p.get_text(" ", strip=True) for p in li.find_all("p")]
        status = next((p for p in ps if p.startswith("Estado")), "")
        out.append({
            "id": m.group(1),
            "court": li.find("h4").get_text(" ", strip=True) if li.find("h4") else "",
            "status": status.replace("Estado:", "").strip(),
            "summary": ps[-1] if ps else "",
        })
    return out


def _tables(html: str) -> list[dict[str, str]]:
    """Each <table> on a detail tab as a {th: td} dict."""
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for t in soup.select("table"):
        row = {}
        for tr in t.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                row[th.get_text(" ", strip=True)] = td.get_text(" ", strip=True)
        if row:
            tables.append(row)
    return tables


def euros(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"([\d.]+,\d{2}|[\d.]+)\s*€", text)
    if not m:
        return None
    v = float(m.group(1).replace(".", "").replace(",", "."))
    return v or None


def rooms_from_text(text: str) -> Optional[int]:
    m = _ROOMS.search(text or "")
    if not m:
        return None
    w = m.group(1).lower()
    return int(w) if w.isdigit() else _NUMWORDS.get(w)


def surface_from_text(text: str) -> Optional[float]:
    vals = [float(v.replace(".", "").replace(",", ".")) for v in _SURFACE.findall(text or "")]
    vals = [v for v in vals if 20 <= v <= 1000]
    return vals[0] if vals else None


_ADDR_NOISE = re.compile(
    r",?\s*\b(?:piso|planta|pl\.|[aá]tico|puerta|pta\.?|esc(?:alera)?\.?|bajos?|entresuelo|"
    r"principal|local|\d+\s*[ºª°]|\d+[ºª°]?\s*-\s*\d+[ºª°]).*$", re.I)


def clean_address(addr: str) -> str:
    """'Calle Aragón, 418, Atico 1ª' -> 'Calle Aragón 418' (Nominatim chokes on floor/door)."""
    a = re.sub(r"\b(?:n[úu]mero|n\.?\s*[ºo°]|n[ºo°])\s*", "", addr or "", flags=re.I)
    a = _ADDR_NOISE.sub("", a)
    a = re.sub(r"\s*,\s*", " ", a)
    return re.sub(r"\s+", " ", a).strip(" ,")


def iso_date(text: str) -> Optional[str]:
    m = re.search(r"ISO:\s*([\dT:+-]+)", text or "")
    return m.group(1) if m else None


class BoeClient:
    def __init__(self, session: PoliteSession, cache_dir: Path):
        self.s = session
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _cached(self, key: str, fetch):
        p = self.cache_dir / f"{re.sub(r'[^A-Za-z0-9_-]', '_', key)}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        data = fetch()
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def detail(self, sub_id: str, ver: int) -> list[dict[str, str]]:
        def get():
            r = self.s.get(DETAIL, params={"idSub": sub_id, "ver": ver})
            r.encoding = "utf-8"
            return _tables(r.text)
        return self._cached(f"{sub_id}_v{ver}", get)

    def catastro(self, refcat: str) -> dict:
        rc = re.sub(r"[^A-Za-z0-9]", "", refcat or "").upper()
        if len(rc) < 14:
            return {}

        def get():
            out: dict = {}
            try:
                j = self.s.get(CATASTRO_COORDS, params={"RefCat": rc[:14], "SRS": "EPSG:4326"}).json()
                geo = j["Consulta_CPMRCResult"]["coordenadas"]["coord"][0]["geo"]
                out["lat"], out["lon"] = float(geo["ycen"]), float(geo["xcen"])
            except (KeyError, IndexError, ValueError, TypeError):
                pass
            try:
                res = self.s.get(CATASTRO_DNP, params={"RefCat": rc if len(rc) == 20 else rc[:14]}
                                 ).json()["consulta_dnprcResult"]
                if "bico" in res:
                    bi = res["bico"]["bi"]
                    out["municipality"] = bi["dt"].get("nm")
                    debi = bi.get("debi") or {}
                    if debi.get("sfc"):
                        out["surface_m2"] = float(debi["sfc"])
                    out["year"] = int(debi["ant"]) if debi.get("ant") else None
                    out["use"] = debi.get("luso")
                elif res.get("lrcdnp"):  # whole building: several units, take the town only
                    out["municipality"] = res["lrcdnp"]["rcdnp"][0]["dt"].get("nm")
            except (KeyError, IndexError, ValueError, TypeError):
                pass
            return out
        return self._cached(f"cat2_{rc}", get)


def fetch(cfg: dict, zone: Zone, session: PoliteSession, geocoder: Geocoder,
          cache_dir: Path) -> Iterator[Listing]:
    client = BoeClient(session, cache_dir)
    for state in cfg["sources"]["boe"].get("states", ["EJ", "PU"]):
        r = session.post(SEARCH, data=search_form(state))
        r.encoding = "utf-8"
        results = parse_search(r.text)
        log.info("boe %s: %d housing auctions in Barcelona province", state, len(results))
        for res in results:
            try:
                yield from _auction(res, state, client, zone, geocoder)
            except Exception as e:  # one broken auction page shouldn't sink the run
                log.warning("boe %s failed: %s", res["id"], e)


def _auction(res: dict, state: str, client: BoeClient, zone: Zone,
             geocoder: Geocoder) -> Iterator[Listing]:
    bienes = [t for t in client.detail(res["id"], 3) if "Localidad" in t]
    in_zone = [b for b in bienes if zone.match(b.get("Localidad", ""))]
    if not in_zone:
        return
    general = next((t for t in client.detail(res["id"], 1) if "Identificador" in t), {})
    for n, b in enumerate(in_zone, 1):
        cat = client.catastro(b.get("Referencia catastral", ""))
        # The court's "Localidad" is sometimes the court's town; Catastro knows better.
        town = cat.get("municipality") or b["Localidad"]
        if not zone.match(town):
            log.info("boe %s: Catastro places it in %s, outside the zone", res["id"], town)
            continue
        lat, lon, approx = cat.get("lat"), cat.get("lon"), False
        if lat is None:
            cp = b.get("Código Postal", "")
            hit = geocoder.geocode(f"{clean_address(b.get('Dirección', ''))}, {cp} {town}, España")
            if not hit and cp:
                hit = geocoder.geocode(f"{cp} {town}, España")
                approx = True
            if hit:
                lat, lon = hit
            else:
                m = zone.match(town)
                lat, lon, approx = m["lat"], m["lon"], True
            if approx and zone.match(town).get("restrict"):
                continue  # can't tell if it's in the allowed part (e.g. Sitges town vs Les Botigues)
        desc = " ".join([b.get("Descripción", ""), res.get("summary", "")])
        value = euros(b.get("Valor Subasta")) or euros(general.get("Valor subasta"))
        appraisal = euros(b.get("Tasación")) or euros(general.get("Tasación"))
        yield Listing(
            source="boe",
            source_id=res["id"] + (f"-{n}" if len(in_zone) > 1 else ""),
            url=f"{DETAIL}?idSub={res['id']}&ver=3",
            price=value or appraisal,
            lat=lat, lon=lon,
            municipality=zone.match(town)["name"],
            title=f"Subasta {res['id']} · {b.get('Dirección', '').title()}",
            rooms=rooms_from_text(desc),
            surface_m2=cat.get("surface_m2") or surface_from_text(desc),
            address=re.sub(r"\s+", " ", f"{b.get('Dirección', '').title()}, {b.get('Código Postal', '')}"),
            property_type="Vivienda (subasta)",
            coords_approx=approx,
            warnings=[w for w in text_warnings(desc) if w != "auction / debt transfer"],
            auction={
                "id": res["id"],
                "state": STATE_LABEL.get(state, state),
                "status": res["status"],
                "type": general.get("Tipo de subasta"),
                "court": res["court"],
                "start": iso_date(general.get("Fecha de inicio", "")),
                "end": iso_date(general.get("Fecha de conclusión", "")),
                "value": value,
                "appraisal": appraisal,
                "min_bid": general.get("Puja mínima"),
                "deposit": euros(b.get("Importe del depósito")) or euros(general.get("Importe del depósito")),
                "claimed": euros(general.get("Cantidad reclamada")),
                "habitual_residence": b.get("Vivienda habitual"),
                "possession": b.get("Situación posesoria"),
                "visitable": b.get("Visitable"),
                "cadastral_ref": b.get("Referencia catastral"),
                "year_built": cat.get("year"),
                "description": b.get("Descripción", "")[:600],
            },
        )
