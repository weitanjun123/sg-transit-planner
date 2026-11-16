# SG Transit Planner

Open-source, AI-assisted screening tool for Singapore's MRT network. It answers two questions with public data:

1. **Where do people live more than a 10-minute walk from an MRT station**, today and after every line LTA has already announced?
2. **Where could new stations and lines go?** The tool gives extra weight to homes whose current station is packed at rush hour, so a new line also takes pressure off crowded lines.

It also includes a **2D pixel MRT simulation**. You can watch trains fill up through the day, tap any train to look inside, and follow a commuter from Jurong East to Raffles Place.

**Live site:** https://weitanjun123.github.io/sg-transit-planner/ (map: `/map.html`, sim: `/sim.html`)

> The proposals are a starting point for planners to examine, not engineering alignments. Real alignments also depend on geology, cost, land and existing underground structures. Everything here is reproducible and every parameter is in the open.

## What's inside

| Path | What it is |
|---|---|
| `data/manual/planned_stations.csv` | Every announced future station: TEL5, DTL3e, Founders' Memorial, JRL Stages 1–3 + JS2A, CRL Phases 1–3, CRL Punggol Extension, Brickland, Sungei Kadut, plus built but unopened stations (Mount Pleasant, Marina South, Bukit Brown). Each row has its opening date and source link |
| `data/manual/future_housing.csv` | 2026 BTO launches (Feb, Jun, Oct) and 2H2026 Government Land Sales sites, with units and source links. Coordinates are approximate |
| `scripts/build_stations.py` | Merges LTA station points, URA Master Plan 2019 station layer and the planned list → `docs/data/stations.json` (stations + track links + study corridors) |
| `scripts/fetch_lta.py` | LTA DataMall: monthly passenger volume per station per hour, plus today's platform crowd forecast and the live crowd level |
| `scripts/fetch_hdb.py` | data.gov.sg HDB Property Information (all ~13k blocks) + OneMap geocoding (cached, resumable) |
| `scripts/analyse.py` | Gap analysis + greedy station siting + line building → `docs/data/analysis.json` |
| `docs/` | GitHub Pages site: `map.html` (Leaflet), `sim.html` (canvas pixel sim) |
| `.github/workflows/data.yml` | Daily data bot (06:17 SGT) |

## Method (short)

1. **Residents.** HDB dwelling units × 3.1 people per unit, summed on a 300 m grid. Future BTO and land-sale units are added at their sites.
2. **Served.** A home counts as served if an MRT station is within ~640 m straight-line (≈800 m walk ≈ 10 minutes). The check is done twice: once against today's network and once against all stations LTA has announced.
3. **Crowding.** Unserved residents are weighted by up to 2× when their current nearest station is busy in the 07:00–09:00 peak. This uses LTA tap-in volumes and LTA's platform crowd forecast.
4. **Siting.** New stations are placed greedily where they serve the most weighted unserved residents. Each one must be at least 1 km from every existing or planned station and serve at least 6,000 people.
5. **Lines.** New sites up to 3 km apart are chained, with no sharp turns, and both ends are tied into the nearest MRT station as interchanges. A single site sitting on an existing line becomes an infill station.

All thresholds are in `PARAMS` at the top of `scripts/analyse.py`.

### Pixel sim model
Train loads are **estimates**. LTA publishes tap-ins and tap-outs per station per hour, not per-train counts. Trips on each line follow a gravity model (boardings × alightings). They are uplifted 1.5× for riders who change lines, then divided by trains per hour and train capacity. Until the LTA data has loaded, the sim runs on a placeholder demand profile and says so on screen.

## Run it yourself

```bash
pip install requests
export LTA_ACCOUNT_KEY=...   # free: https://datamall.lta.gov.sg/content/datamall/en/request-for-api.html
python scripts/fetch_lta.py
python scripts/fetch_hdb.py     # first run geocodes in batches of 4,000 blocks
python scripts/analyse.py
cd docs && python -m http.server   # open http://localhost:8000
```

## Data sources & licences
- LTA DataMall: passenger volume by train station, platform crowd density (real-time and forecast)
- URA Master Plan 2019 Rail Station layer (data.gov.sg, Singapore Open Data Licence)
- HDB Property Information (data.gov.sg, Singapore Open Data Licence)
- OneMap (SLA) search API for geocoding
- LTA station points via [cheeaun/sgraildata](https://github.com/cheeaun/sgraildata)
- LTA, URA and HDB press releases for planned stations, BTO launches and land sales (linked per row)

## Contributing
New LTA announcement? Edit `data/manual/planned_stations.csv` (add the source link) and the station list rebuilds automatically. New BTO launch or land-sale site? Add a row to `data/manual/future_housing.csv`.

MIT licensed.
