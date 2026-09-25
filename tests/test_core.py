from pathlib import Path

import pytest

from premium.geo import Zone, norm_name
from premium.models import Listing
from premium.pipeline import dedupe, load_config
from premium.scoring import affordability, meets_criteria, proximity, score, work_score
from premium.sources.boe import euros, parse_search, rooms_from_text, surface_from_text

CFG = load_config(Path(__file__).resolve().parent.parent / "config.yaml")


def mk(**kw) -> Listing:
    base = dict(source="fotocasa", source_id="1", url="u", price=300000, lat=41.41, lon=2.02,
                municipality="Molins de Rei", rooms=3, surface_m2=80)
    base.update(kw)
    return Listing(**base)


def test_proximity_is_linear_between_bounds():
    assert proximity(1, 3, 30) == 1.0
    assert proximity(30, 3, 30) == 0.0
    assert proximity(16.5, 3, 30) == pytest.approx(0.5)


def test_work_score_rewards_balance():
    balanced = work_score(0.6, 0.6, 0.5)
    lopsided = work_score(1.0, 0.2, 0.5)  # same average, one hub far away
    assert balanced > lopsided


def test_affordability_cheaper_is_better():
    b = CFG["affordability"]
    assert affordability(180000, b["best_price"], b["worst_price"]) == 1.0
    assert affordability(300000, b["best_price"], b["worst_price"]) == pytest.approx(0.5)
    assert affordability(420000, b["best_price"], b["worst_price"]) == 0.0


def test_criteria_gate():
    c = CFG["criteria"]
    assert meets_criteria(mk(), c)
    assert meets_criteria(mk(price=350000), c)
    assert not meets_criteria(mk(price=360000), c)
    assert not meets_criteria(mk(rooms=2), c)
    assert not meets_criteria(mk(surface_m2=65), c)
    assert meets_criteria(mk(rooms=None, surface_m2=None), c)  # auctions with unknowns stay in


def test_index_uses_config_weights():
    s = score(mk(), CFG)
    c = s["components"]
    w = CFG["weights"]
    expected = sum(w[k] * c[k] for k in w) / sum(w.values()) * 100
    assert s["index"] == pytest.approx(expected, abs=0.1)
    # between Sant Cugat and Castelldefels beats Barcelona centre for this weighting
    assert s["index"] > score(mk(lat=41.387, lon=2.169, municipality="Barcelona"), CFG)["index"]


def test_zone_names_and_botigues():
    z = Zone(CFG["municipalities"])
    assert norm_name("L'Hospitalet de Llobregat") == norm_name("Hospitalet de Llobregat (L')")
    assert z.contains("Gavà", 41.30, 2.00)
    assert z.contains("GAVA", 41.30, 2.00)
    assert z.contains("Barcelona Capital", 41.39, 2.17)
    assert z.contains("Sitges", 41.2650, 1.9420)          # Les Botigues
    assert not z.contains("Sitges", 41.2370, 1.8060)      # Sitges town
    assert z.contains("Sitges", None, None, district="Les Botigues de Sitges")
    assert not z.contains("Terrassa", 41.56, 2.01)
    assert not z.contains("Rubí", 41.49, 2.03)            # extended ring is off by default


def test_dedupe_merges_same_flat_across_portals():
    a = mk(source="fotocasa", source_id="1", coords_approx=True)
    b = mk(source="habitaclia", source_id="9", lat=41.412, lon=2.021, url="hab")
    c = mk(source="habitaclia", source_id="10", price=250000)
    out = dedupe([b, a, c])
    assert len(out) == 2
    kept = next(l for l in out if l.source == "fotocasa")
    assert kept.also_on == [{"source": "habitaclia", "url": "hab"}]
    assert kept.coords_approx is False  # took the more precise pin


def test_boe_text_parsing():
    assert euros("351.040,48 €") == pytest.approx(351040.48)
    assert euros("0,00 €") is None
    assert rooms_from_text("VIVIENDA de 85 m2, compuesta de tres dormitorios, baño") == 3
    assert rooms_from_text("distribuida en 4 habitaciones") == 4
    assert surface_from_text("superficie construida de 132m2 y útil 119 m2") == 132


def test_boe_search_parsing():
    html = """<ul><li class="resultado-busqueda"><h3>SUBASTA SUB-JA-2026-259705</h3>
      <h4>Juzgado Martorell</h4><p>Expediente: 0274/14</p>
      <p>Estado: Celebrándose - [Conclusión prevista: 15/10/2026]</p><p>URBANA.- VIVIENDA</p></li></ul>"""
    [r] = parse_search(html)
    assert r["id"] == "SUB-JA-2026-259705"
    assert r["status"].startswith("Celebrándose")
    assert r["summary"] == "URBANA.- VIVIENDA"


def test_dedupe_keeps_different_floors_apart():
    a = mk(source_id="1", floor="2")
    b = mk(source_id="2", floor="5")
    assert len(dedupe([a, b])) == 2


def test_boe_address_cleaning():
    from premium.sources.boe import clean_address
    assert clean_address("Calle Aragón, 418, Atico 1ª") == "Calle Aragón 418"
    assert clean_address("Avenida Onze De Setembre, Número 58, 1º-2ª") == "Avenida Onze De Setembre 58"
    assert clean_address("Calle Planeta 5") == "Calle Planeta 5"


def test_dedupe_same_photo_merges_despite_floor_mismatch():
    a = mk(source="fotocasa", source_id="1", floor="7", image="https://static.fotocasa.es/images/ads/x?rule=original")
    b = mk(source="habitaclia", source_id="2", floor="SECOND", image="https://static.fotocasa.es/images/ads/x?rule=original")
    assert len(dedupe([a, b])) == 1
