# idealista_premium

Home search across **Fotocasa**, **Habitaclia**, **Idealista** (official API) and **BOE public auctions**, limited to the area between Barcelona, Castelldefels (+ Les Botigues de Sitges) and Sant Cugat. Every listing gets a 0–100 index, and everything is shown on one interactive map.

## Quick start

```bash
pip install -r requirements.txt
python -m premium run --open        # scrape everything (~30-40 min), build output/map.html and open it
```

Other commands:

```bash
python -m premium build --open                       # re-score + rebuild the map from saved data, no network
python -m premium scrape --sources boe               # refresh only the auctions (~1 min once cached)
python -m premium scrape --only gava molins-de-rei   # refresh some municipalities (merged into saved data)
python -m pytest                                     # tests
```

Outputs, in `output/`:

| File | What |
|---|---|
| `map.html` | The interactive map. Self-contained: open it from disk, no server needed. |
| `listings.csv` | Every listing in the zone with its scores (`;`-separated, opens in Excel). |
| `listings.json` | The same, with every field. |

## The map

- **Pins**: circles are portal listings, squares are BOE auctions. Colour = index, from light blue (low) to dark blue (high); the legend shows the current range. A dashed outline means the pin is approximate (the portal hides the exact address).
- **Index weights**: four sliders; the index and colours update live. Reset returns to 20 / 30 / 30 / 20.
- **Filters**: price, surface, bedrooms, minimum index, sources, municipalities. They start at your criteria (≥3 bedrooms, ≥70 m², ≤350k €). Loosen them to see near-misses up to 400k.
- **Risky listings** (bare ownership, occupied, sold with tenants, partial shares) are flagged and hidden by default.
- **★ Save** and **✕ Discard** are remembered in your browser. "Only favourites" shows just your shortlist.
- The same flat posted on several portals, or several times by different agencies, is shown once. The popup links to every copy.
- Clicking a pin updates the URL (`map.html#l=fotocasa:190388318`), so you can bookmark or send a specific flat.

## How the score works

**Criteria gate** (`meets_criteria`): price ≤ 350k, ≥ 3 bedrooms, ≥ 70 m². Auctions often don't state bedrooms or surface; unknown values don't disqualify.

**Index** = weighted sum of four 0–1 components × 100:

| Component | Weight | How |
|---|---|---|
| Work balance | 20 % | Closeness to Terrassa centre and to Barcelona, blended as 50 % *average* + 50 % *worst of the two*, so being next to one hub but far from the other is penalised. Also shown as a 0–10 "work rating". |
| Near Sant Cugat | 30 % | Closeness to Sant Cugat (Monestir) |
| Near Castelldefels | 30 % | Closeness to Castelldefels centre |
| Affordability | 20 % | 200k € → 1.0, falling linearly to 0 at 400k €. Cheaper is better, but it's only 20 %. |

Closeness = 1.0 within 3 km, falling linearly to 0 at 30 km, measured in a straight line. Every number (weights, anchors, distances, price band) is in [config.yaml](config.yaml).

## The zone

Barcelona, L'Hospitalet, Esplugues, Sant Just Desvern, Sant Joan Despí, Cornellà, Sant Feliu, Molins de Rei, Pallejà, Sant Vicenç dels Horts, Santa Coloma de Cervelló, Torrelles, Sant Boi, El Prat, Viladecans, Gavà, Castelldefels, Begues, Sant Cugat (incl. Valldoreix / La Floresta / Mira-sol) and **Les Botigues de Sitges only**: Sitges listings must be within 1.8 km of Les Botigues or have it as their district.

A second ring is in the config but off by default: Cerdanyola, Rubí, Sant Andreu de la Barca, Cervelló, Vallirana and Castellbisbal. Set `enabled: true` to add any of them.

## Sources, and what to know about each

| Source | How | Notes |
|---|---|---|
| Fotocasa | Search pages; results are embedded JSON | Asks the portal for ≥3 bedrooms, ≥70 m², ≤400k. Includes occupied / bare-ownership flags. |
| Habitaclia | Search pages; embedded JSON | Same company group as Fotocasa, so most listings overlap and get merged. |
| BOE subastas | Advanced search for housing in Barcelona province, then each auction's detail pages | *Celebrándose* and *Próxima apertura*. Coordinates, built surface and year come from the **Catastro** public service via the cadastral reference; the municipality is also taken from Catastro, because the court's "Localidad" is sometimes wrong. The price shown is the *valor de subasta*. Check deposit, possession status and charges before bidding. |
| Idealista | **Official API** only | idealista.com blocks scrapers, and this project does not try to get around that. Request a key at <https://developers.idealista.com/access-request>, then put `IDEALISTA_API_KEY` / `IDEALISTA_API_SECRET` in a `.env` file (see `.env.example`). Without a key the source is skipped. |

Being a good citizen: one request at a time, with random 1.5–3.5 s pauses and backoff on errors. A 403 stops that source instead of retrying. Geocoding uses Nominatim (OpenStreetMap) at ≤1 req/s, with an on-disk cache. Portal terms generally restrict automated access and reuse of their data, so keep this for your own home search.

If a portal changes its page layout, its source fails with a clear message and the data you already have is kept. The parsers are small; see `premium/sources/`.

## Layout

```
config.yaml              criteria, weights, anchors, municipalities, sources
premium/
  __main__.py            CLI (run / scrape / build)
  pipeline.py            scrape -> zone filter -> dedupe -> score -> outputs
  scoring.py             criteria gate + index
  geo.py                 distances, zone test, Nominatim geocoder
  http.py                slow, polite HTTP session
  mapgen.py              writes output/map.html
  templates/map.html     the map UI (Leaflet); mirrors the index formula in JS
  sources/               fotocasa, habitaclia, boe, idealista
data/                    raw snapshots + caches (git-ignored)
output/                  map + exports (git-ignored)
```

---

## Original brief

i want to create a idealista premium, i want to scrap idealista data, fotocasa data, habitaclia data, and also the pujas libres from the state of spain https://subastas.boe.es/ https://subastas.boe.es/subastas_ava.php

only for zones between barcelona, castelldefels (also includes "les botigues de sitges") and sant cugat (included), also you have to think abput the different municiplaities like molines de rei, palleja, gava...
i want to create a puntuation based on 3 habitacions minim and also minim 70m2 < 350k. also create a rating that priorisize  the balance between proximity to terrasa centre and to barcelona because it is important to be connected to where the people work. Finally i want to create some index that englobes, proximity to work, affordability the prices should be between 200-400k€ so it is important to consider it cheaper better but not the most important
close to work --> 20%
close to sant cugat --> 30%
close to castelldefels --> 30%
affordability --> 20%

finally i want to see all the possibilities regarding my filters and i want to see them in a map, and with a well designed interactive map where i can see every anouncement.
