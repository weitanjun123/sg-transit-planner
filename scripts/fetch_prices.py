"""Home prices, saved in the repo and reused.

HDB resale (no key): data.gov.sg "Resale flat prices based on registration date from Jan-2017 onwards"
  (d_8b84c4ee58e3cfc0ece0d773c8ca6abc), full CSV re-downloaded at most every 7 days -> data/raw/prices/hdb_resale.csv
Private (condo / EC / landed): URA Data Service PMI_Resi_Transaction, needs env URA_ACCESS_KEY (free:
  https://eservice.ura.gov.sg/maps/api/reg.html). URA serves a rolling ~5 years in 4 batches; every transaction is kept
  forever in data/raw/prices/ura_transactions.json (deduplicated), so history keeps growing after URA drops it.
"""
import csv, hashlib, io, json, math, os, sys, time, datetime as dt
import requests

RAW = "data/raw/prices"
HDB_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
UA = {"User-Agent": "Mozilla/5.0 (sg-transit-planner; +https://github.com/weitanjun123/sg-transit-planner)"}


def fresh(path, days):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < days * 86400


def hdb_resale():
    path = f"{RAW}/hdb_resale.csv"
    if fresh(path, 7):
        print("HDB resale: reusing saved file"); return
    j = {}
    for _ in range(12):
        j = requests.get(f"https://api-open.data.gov.sg/v1/public/api/datasets/{HDB_ID}/poll-download", timeout=60).json()
        if j.get("code") == 0 and j.get("data", {}).get("url"):
            break
        time.sleep(10)
    text = requests.get(j["data"]["url"], timeout=300).text
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) < 100000:
        sys.exit(f"HDB resale: only {len(rows)} rows, keeping the old file")
    open(path, "w", encoding="utf-8").write(text)
    print(f"HDB resale: {len(rows)} sales, {rows[0]['month']} to {max(r['month'] for r in rows)}")


# ---- SVY21 (Singapore grid used by URA) -> WGS84 lat/lon
def svy21_to_latlon(N, E):
    a, f = 6378137.0, 1 / 298.257223563
    oLat, oLon, oN, oE, k = 1.366666, 103.833333, 38744.572, 28001.642, 1.0
    b = a * (1 - f); e2 = 2 * f - f * f; e4, e6 = e2 * e2, e2 ** 3
    A0 = 1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256; A2 = 3 / 8 * (e2 + e4 / 4 + 15 * e6 / 128)
    A4 = 15 / 256 * (e4 + 3 * e6 / 4); A6 = 35 * e6 / 3072
    M = lambda lat: a * (A0 * lat - A2 * math.sin(2 * lat) + A4 * math.sin(4 * lat) - A6 * math.sin(6 * lat))
    n = (a - b) / (a + b); n2, n3, n4 = n * n, n ** 3, n ** 4
    G = a * (1 - n) * (1 - n2) * (1 + 9 * n2 / 4 + 225 * n4 / 64) * (math.pi / 180)
    Mo = M(math.radians(oLat)); Mp = Mo + (N - oN) / k
    sigma = Mp * math.pi / (180 * G)
    lat1 = sigma + (3 * n / 2 - 27 * n3 / 32) * math.sin(2 * sigma) + (21 * n2 / 16 - 55 * n4 / 32) * math.sin(4 * sigma) \
        + (151 * n3 / 96) * math.sin(6 * sigma) + (1097 * n4 / 512) * math.sin(8 * sigma)
    s1 = math.sin(lat1); rho1 = a * (1 - e2) / (1 - e2 * s1 * s1) ** 1.5; v1 = a / math.sqrt(1 - e2 * s1 * s1)
    psi1 = v1 / rho1; t1 = math.tan(lat1); Ep = E - oE; x = Ep / (k * v1)
    lat = lat1 - (t1 / (k * rho1)) * (x * Ep / 2) + (t1 / (k * rho1)) * (Ep * x ** 3 / 24) * (-4 * psi1 ** 2 + 9 * psi1 * (1 - t1 ** 2) + 12 * t1 ** 2) \
        - (t1 / (k * rho1)) * (Ep * x ** 5 / 720) * (8 * psi1 ** 4 * (11 - 24 * t1 ** 2) - 12 * psi1 ** 3 * (21 - 71 * t1 ** 2) + 15 * psi1 ** 2 * (15 - 98 * t1 ** 2 + 15 * t1 ** 4) + 180 * psi1 * (5 * t1 ** 2 - 3 * t1 ** 4) + 360 * t1 ** 4) \
        + (t1 / (k * rho1)) * (Ep * x ** 7 / 40320) * (1385 + 3633 * t1 ** 2 + 4095 * t1 ** 4 + 1575 * t1 ** 6)
    sec = 1 / math.cos(lat1)
    lon = math.radians(oLon) + sec * x - sec * x ** 3 / 6 * (psi1 + 2 * t1 ** 2) \
        + sec * x ** 5 / 120 * (-4 * psi1 ** 3 * (1 - 6 * t1 ** 2) + psi1 ** 2 * (9 - 68 * t1 ** 2) + 72 * psi1 * t1 ** 2 + 24 * t1 ** 4) \
        - sec * x ** 7 / 5040 * (61 + 662 * t1 ** 2 + 1320 * t1 ** 4 + 720 * t1 ** 6)
    return round(math.degrees(lat), 6), round(math.degrees(lon), 6)


def ura_private():
    key = os.environ.get("URA_ACCESS_KEY")
    if not key:
        print("URA: no URA_ACCESS_KEY secret yet, skipping condos/landed"); return
    path = f"{RAW}/ura_transactions.json"
    if fresh(path, 6):
        print("URA: reusing saved file (refreshed weekly)"); return
    tok = requests.get("https://eservice.ura.gov.sg/uraDataService/insertNewToken/v1", headers={**UA, "AccessKey": key}, timeout=60).json()
    token = tok.get("Result")
    if not token:
        sys.exit(f"URA token failed: {tok}")
    store = json.load(open(path)) if os.path.exists(path) else {"note": "URA PMI_Resi_Transaction, every transaction ever fetched", "tx": {}}
    new = 0
    for batch in (1, 2, 3, 4):
        r = requests.get("https://eservice.ura.gov.sg/uraDataService/invokeUraDS/v1", params={"service": "PMI_Resi_Transaction", "batch": batch},
                         headers={**UA, "AccessKey": key, "Token": token}, timeout=300)
        res = r.json().get("Result", [])
        for p in res:
            lat = lon = None
            if p.get("x") and p.get("y"):
                lat, lon = svy21_to_latlon(float(p["y"]), float(p["x"]))
            for t in p.get("transaction", []):
                rec = {"project": p.get("project"), "street": p.get("street"), "segment": p.get("marketSegment"), "lat": lat, "lon": lon,
                       "area_sqm": float(t.get("area") or 0), "floor": t.get("floorRange"), "units": int(t.get("noOfUnits") or 1),
                       "contract": t.get("contractDate"), "sale": t.get("typeOfSale"), "price": float(t.get("price") or 0),
                       "type": t.get("propertyType"), "district": t.get("district"), "area_type": t.get("typeOfArea"), "tenure": t.get("tenure")}
                h = hashlib.sha1(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:16]
                if h not in store["tx"]:
                    store["tx"][h] = rec; new += 1
        print(f"URA batch {batch}: {len(res)} projects")
        time.sleep(2)
    store["updated"] = dt.date.today().isoformat()
    json.dump(store, open(path, "w"), separators=(",", ":"))
    print(f"URA: {len(store['tx'])} private transactions stored ({new} new)")


if __name__ == "__main__":
    os.makedirs(RAW, exist_ok=True)
    try:
        hdb_resale()
    except Exception as e:
        print(f"HDB resale: {e}")
    try:
        ura_private()
    except Exception as e:
        print(f"URA: {e}")
