"""Fetch every HDB block (data.gov.sg "HDB Property Information") and geocode it with OneMap.

Both are free and need no key. Results are saved in the repo and reused:
  data/raw/hdb_property_info.json   refreshed at most once a month
  data/raw/geocode_cache.json       each block is looked up once, ever (resumes across runs)
  docs/data/hdb_blocks.json         [[lat, lon, dwelling_units, year_completed, town], ...]
"""
import json, os, sys, time, datetime as dt
import requests

DATASET = "d_17f5382f26140b1fdae0ba2ef6239d2f"
RAW = "data/raw"
CACHE = f"{RAW}/geocode_cache.json"
MAX_LOOKUPS = int(os.environ.get("MAX_GEOCODE", "4000"))  # OneMap allows ~250 calls/min
TIME_BUDGET = int(os.environ.get("GEOCODE_SECONDS", "1500"))  # stop early so the run always gets to commit


def fetch_property_info():
    path = f"{RAW}/hdb_property_info.json"
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 28 * 86400:
        return json.load(open(path))["records"]
    recs, offset = [], 0
    while True:
        r = requests.get("https://data.gov.sg/api/action/datastore_search",
                         params={"resource_id": DATASET, "limit": 5000, "offset": offset}, timeout=60)
        if r.status_code == 429:
            time.sleep(15); continue
        r.raise_for_status()
        res = r.json()["result"]
        recs += res["records"]
        offset += 5000
        if offset >= res["total"]:
            break
        time.sleep(3)
    json.dump({"fetched": dt.date.today().isoformat(), "records": recs}, open(path, "w"))
    print(f"HDB property info: {len(recs)} blocks")
    return recs


def geocode(blk, street, session):
    r = session.get("https://www.onemap.gov.sg/api/common/elastic/search",
                    params={"searchVal": f"{blk} {street}", "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1}, timeout=30)
    if r.status_code == 429:
        time.sleep(30); return "retry"
    r.raise_for_status()
    results = r.json().get("results", [])
    for x in results:
        if x.get("BLK_NO", "").upper() == blk.upper():
            return [round(float(x["LATITUDE"]), 6), round(float(x["LONGITUDE"]), 6)]
    return [round(float(results[0]["LATITUDE"]), 6), round(float(results[0]["LONGITUDE"]), 6)] if results else None


def main():
    os.makedirs(RAW, exist_ok=True); os.makedirs("docs/data", exist_ok=True)
    recs = [r for r in fetch_property_info() if r.get("residential") == "Y"]
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    s, done, t0 = requests.Session(), 0, time.time()
    for r in recs:
        k = f"{r['blk_no']}|{r['street']}"
        if k in cache or done >= MAX_LOOKUPS or time.time() - t0 > TIME_BUDGET:
            continue
        g = geocode(r["blk_no"], r["street"], s)
        if g == "retry":
            continue
        cache[k] = g; done += 1
        time.sleep(0.25)
        if done % 500 == 0:
            json.dump(cache, open(CACHE, "w")); print(f"geocoded {done}")
    json.dump(cache, open(CACHE, "w"))
    blocks = []
    for r in recs:
        g = cache.get(f"{r['blk_no']}|{r['street']}")
        if g:
            blocks.append([g[0], g[1], int(r.get("total_dwelling_units") or 0), int(r.get("year_completed") or 0), r.get("bldg_contract_town", "")])
    json.dump(blocks, open("docs/data/hdb_blocks.json", "w"), separators=(",", ":"))
    missing = sum(1 for r in recs if f"{r['blk_no']}|{r['street']}" not in cache)
    print(f"hdb_blocks.json: {len(blocks)} located blocks, {missing} still to geocode (next run continues)")


if __name__ == "__main__":
    main()
