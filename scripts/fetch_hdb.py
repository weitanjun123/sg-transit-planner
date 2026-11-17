"""Locate every HDB residential block with its number of flats. Free data.gov.sg datasets only, no key.

  HDB Property Information (d_17f5382f26140b1fdae0ba2ef6239d2f): block no, street, town, dwelling units, year
  HDB Existing Buildings   (d_16b157c52ed637edd6ba1232e026258d): block footprint polygons with block no

The two are joined on block number + town: block numbers are (almost always) unique within an HDB town, so
for each block we take the footprint with that number closest to the town centre (max 6 km).
Raw files are saved in data/raw and re-downloaded at most once every 28 days.
Output: docs/data/hdb_blocks.json  [[lat, lon, dwelling_units, year_completed, town], ...]
"""
import json, math, os, time, datetime as dt
from collections import defaultdict
import requests

RAW = "data/raw"
PROPERTY = "d_17f5382f26140b1fdae0ba2ef6239d2f"
BUILDINGS = "d_16b157c52ed637edd6ba1232e026258d"
# HDB town code -> (name, approx town-centre lat, lon)
TOWNS = {
    "AMK": ("Ang Mo Kio", 1.3700, 103.8496), "BB": ("Bukit Batok", 1.3490, 103.7496), "BD": ("Bedok", 1.3240, 103.9300),
    "BH": ("Bishan", 1.3510, 103.8485), "BM": ("Bukit Merah", 1.2820, 103.8180), "BP": ("Bukit Panjang", 1.3790, 103.7620),
    "BT": ("Bukit Timah", 1.3300, 103.7900), "CCK": ("Choa Chu Kang", 1.3850, 103.7445), "CL": ("Clementi", 1.3150, 103.7650),
    "CT": ("Central Area", 1.2900, 103.8450), "GL": ("Geylang", 1.3200, 103.8850), "HG": ("Hougang", 1.3710, 103.8925),
    "JE": ("Jurong East", 1.3400, 103.7400), "JW": ("Jurong West", 1.3450, 103.7050), "KWN": ("Kallang/Whampoa", 1.3150, 103.8600),
    "MP": ("Marine Parade", 1.3030, 103.9050), "PG": ("Punggol", 1.4050, 103.9020), "PRC": ("Pasir Ris", 1.3730, 103.9490),
    "QT": ("Queenstown", 1.2950, 103.8050), "SB": ("Sembawang", 1.4490, 103.8200), "SGN": ("Serangoon", 1.3550, 103.8700),
    "SK": ("Sengkang", 1.3910, 103.8950), "TAP": ("Tampines", 1.3530, 103.9450), "TG": ("Tengah", 1.3600, 103.7350),
    "TP": ("Toa Payoh", 1.3330, 103.8470), "WL": ("Woodlands", 1.4370, 103.7860), "YS": ("Yishun", 1.4290, 103.8350),
}


def fresh(path):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < 28 * 86400


def property_info():
    path = f"{RAW}/hdb_property_info.json"
    if fresh(path):
        return json.load(open(path))["records"]
    recs, offset = [], 0
    while True:
        r = requests.get("https://data.gov.sg/api/action/datastore_search",
                         params={"resource_id": PROPERTY, "limit": 5000, "offset": offset}, timeout=60)
        if r.status_code == 429:
            time.sleep(15); continue
        r.raise_for_status()
        res = r.json()["result"]; recs += res["records"]; offset += 5000
        if offset >= res["total"]:
            break
        time.sleep(3)
    json.dump({"fetched": dt.date.today().isoformat(), "records": recs}, open(path, "w"))
    return recs


def buildings():
    path = f"{RAW}/hdb_existing_buildings_points.json"
    if fresh(path):
        return json.load(open(path))
    j = {}
    for _ in range(10):
        j = requests.get(f"https://api-open.data.gov.sg/v1/public/api/datasets/{BUILDINGS}/poll-download", timeout=60).json()
        if j.get("code") == 0 and j.get("data", {}).get("url"):
            break
        time.sleep(10)
    gj = requests.get(j["data"]["url"], timeout=300).json()
    pts = []
    for f in gj["features"]:
        g = f["geometry"]
        rings = [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]
        ring = max(rings, key=len)
        lon = sum(c[0] for c in ring) / len(ring); lat = sum(c[1] for c in ring) / len(ring)
        p = f["properties"]
        pts.append([str(p.get("BLK_NO", "")).strip().upper(), round(lat, 6), round(lon, 6), str(p.get("POSTAL_COD", ""))])
    json.dump(pts, open(path, "w"), separators=(",", ":"))
    return pts


def km(a, b, c, d):
    return math.hypot((b - d) * 111.32 * math.cos(math.radians(1.35)), (a - c) * 110.57)


def main():
    os.makedirs(RAW, exist_ok=True); os.makedirs("docs/data", exist_ok=True)
    recs = [r for r in property_info() if r.get("residential") == "Y"]
    by_blk = defaultdict(list)
    for blk, lat, lon, postal in buildings():
        by_blk[blk].append((lat, lon))
    cp = f"{RAW}/geocode_cache.json"   # exact OneMap points from earlier runs, used when present
    cache = json.load(open(cp)) if os.path.exists(cp) else {}
    blocks, missed, used, coords = [], defaultdict(int), set(), {}
    for r in recs:
        town = TOWNS.get(r.get("bldg_contract_town"), ("", None, None))
        exact = cache.get(f"{r['blk_no']}|{r['street']}")
        if exact:
            lat, lon = exact
        else:
            cands = [c for c in by_blk.get(r["blk_no"].strip().upper(), []) if c not in used]
            if town[1] is None or not cands:
                missed[r.get("bldg_contract_town")] += 1; continue
            d, lat, lon = min((km(town[1], town[2], la, lo), la, lo) for la, lo in cands)
            if d > 6:
                missed[r.get("bldg_contract_town")] += 1; continue
            used.add((lat, lon))
        blocks.append([lat, lon, int(r.get("total_dwelling_units") or 0), int(r.get("year_completed") or 0), town[0]])
        coords[f"{r['blk_no'].strip().upper()}|{r['street'].strip().upper()}"] = [lat, lon, town[0]]
    json.dump(blocks, open("docs/data/hdb_blocks.json", "w"), separators=(",", ":"))
    json.dump(coords, open(f"{RAW}/hdb_block_coords.json", "w"), separators=(",", ":"))   # used to place resale prices
    units = sum(b[2] for b in blocks)
    print(f"hdb_blocks.json: {len(blocks)}/{len(recs)} residential blocks located ({units:,} flats); not matched: {dict(missed)}")


if __name__ == "__main__":
    main()
