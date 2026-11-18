"""Downloads for the new-town comparison (free data.gov.sg, no key)

  data/raw/prices/history_newtowns.json  HDB resales 1990-2016 for Punggol, Sengkang, Tengah + all-HDB monthly median $psf
                                         (built once from HDB's four historical resale files; they no longer change)
  data/raw/prices/hdb_rpi.json           HDB Resale Price Index, quarterly (refreshed every 30 days)
  data/raw/prices/bto_price_range.json   BTO launch price ranges by financial year, town and flat type (refreshed every 30 days)
"""
import csv, io, json, os, statistics, time
import requests

OUT = "data/raw/prices"
HISTORY = ["d_ebc5ab87086db484f88045b47411ebc5",   # 1990-1999 (approval date)
           "d_43f493c6c50d54243cc1eab0df142d6a",   # 2000-Feb 2012 (approval date)
           "d_2d5ff9ea31397b66239f245f57751537",   # Mar 2012-Dec 2014 (registration date)
           "d_ea9ed51da2787afaf8e51f827c304208"]   # 2015-2016 (registration date)
RPI = "d_14f63e595975691e7c24a27ae4c07c79"
BTO = "d_2d493bdcc1d9a44828b6e71cb095b88d"
TOWNS = {"PUNGGOL", "SENGKANG", "TENGAH"}
SQFT = 10.7639


def fresh(p, days):
    return os.path.exists(p) and time.time() - os.path.getmtime(p) < days * 86400


def poll_download(did):
    for _ in range(12):
        j = requests.get(f"https://api-open.data.gov.sg/v1/public/api/datasets/{did}/poll-download", timeout=60).json()
        if j.get("code") == 0 and j.get("data", {}).get("url"):
            return requests.get(j["data"]["url"], timeout=600).text
        time.sleep(10)
    raise RuntimeError(f"{did}: {j}")


def datastore(did):
    recs, off = [], 0
    while True:
        j = requests.get("https://data.gov.sg/api/action/datastore_search", params={"resource_id": did, "limit": 1000, "offset": off}, timeout=60).json()
        got = j.get("result", {}).get("records", [])
        recs += got; off += len(got)
        if len(got) < 1000:
            return recs


def main():
    os.makedirs(OUT, exist_ok=True)
    hp = f"{OUT}/history_newtowns.json"
    if not os.path.exists(hp):
        rows, allm = [], {}
        for did in HISTORY:
            try:
                text = poll_download(did)
            except Exception as e:
                print(f"history {did}: {e}"); return
            month_psf = {}
            for r in csv.DictReader(io.StringIO(text)):
                try:
                    area = float(r.get("floor_area_sqm") or 0); price = float(r["resale_price"])
                except (ValueError, KeyError):
                    continue
                if area <= 0:
                    continue
                m = r["month"]
                month_psf.setdefault(m, []).append(price / (area * SQFT))
                if r["town"] in TOWNS:
                    rows.append([m, r["town"], r["flat_type"], r["block"], r["street_name"], area, r.get("lease_commence_date", ""), price])
            for m, v in month_psf.items():
                allm[m] = round(statistics.median(v), 1)
            print(f"history {did}: {len(month_psf)} months")
        json.dump({"all_hdb_median_psf": allm, "rows": rows, "columns": ["month", "town", "flat_type", "block", "street", "area_sqm", "lease_commence", "price"]},
                  open(hp, "w"), separators=(",", ":"))
        print(f"history: {len(rows)} Punggol/Sengkang/Tengah resales 1990-2016")
    for did, name in [(RPI, "hdb_rpi"), (BTO, "bto_price_range")]:
        p = f"{OUT}/{name}.json"
        if fresh(p, 30):
            continue
        try:
            recs = datastore(did)
            if recs:
                json.dump({"source": f"https://data.gov.sg/datasets/{did}/view", "records": recs}, open(p, "w"), indent=0)
                print(f"{name}: {len(recs)} rows; towns: {sorted({r.get('town', '') for r in recs})[:40]}")
        except Exception as e:
            print(f"{name}: {e}")


if __name__ == "__main__":
    main()
