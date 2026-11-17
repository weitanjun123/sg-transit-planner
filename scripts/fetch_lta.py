"""Fetch MRT crowd data from LTA DataMall (free account key: env LTA_ACCOUNT_KEY).

Saves raw data under data/raw/lta/ and reuses it - each month's passenger volume file is downloaded once.

  PV/Train         monthly tap-in / tap-out per station per hour (weekday + weekend)  -> docs/data/station_hourly.json
  PCDForecast      today's forecast platform crowd level per station, 30-min slots     -> docs/data/crowd_forecast.json
  PCDRealTime      current platform crowd level per station                             -> docs/data/crowd_now.json
"""
import csv, io, json, os, re, sys, zipfile, datetime as dt
import requests

BASE = "https://datamall2.mytransport.sg/ltaodataservice"
LINES = ["NSL", "EWL", "CGL", "NEL", "CCL", "CEL", "DTL", "TEL", "BPL", "SLRT", "PLRT"]
RAW = "data/raw/lta"
OUT = "docs/data"
SGT = dt.timezone(dt.timedelta(hours=8))


def get(path, key, **params):
    r = requests.get(f"{BASE}/{path}", headers={"AccountKey": key, "accept": "application/json"}, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def passenger_volume(key):
    """Download every month LTA still offers (it keeps ~3) and keep each one forever in data/raw/lta/."""
    os.makedirs(RAW, exist_ok=True)
    d = dt.datetime.now(SGT).date().replace(day=1)
    for _ in range(4):
        d = (d - dt.timedelta(days=1)).replace(day=1)
        ym = d.strftime("%Y%m")
        raw_path = f"{RAW}/pv_train_{ym}.csv"
        if os.path.exists(raw_path):
            print(f"PV/Train {ym}: already archived"); continue
        try:
            link = get("PV/Train", key, Date=ym)["value"][0]["Link"]
        except Exception as e:
            print(f"PV/Train {ym}: not available ({e})"); continue
        z = zipfile.ZipFile(io.BytesIO(requests.get(link, timeout=120).content))
        name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        open(raw_path, "wb").write(z.read(name))
        print(f"PV/Train {ym}: downloaded {name}")
    saved = sorted(f for f in os.listdir(RAW) if re.match(r"pv_train_\d{6}\.csv$", f))
    if saved:
        build_hourly(f"{RAW}/{saved[-1]}", saved[-1][9:15])
        build_history(saved)


def day_counts(ym):
    """Weekdays and weekend days in a month (public holidays are not removed)."""
    y, m = int(ym[:4]), int(ym[4:])
    d, wk, we = dt.date(y, m, 1), 0, 0
    while d.month == m:
        if d.weekday() < 5: wk += 1
        else: we += 1
        d += dt.timedelta(days=1)
    return {"weekday": wk, "weekend": we}


def build_history(saved):
    """docs/data/pv_history.json: every archived month, monthly totals per station per hour (in, out)."""
    hist = {"source": "LTA DataMall PV/Train, archived monthly by the data bot", "months": [], "days": {}, "data": {}}
    for f in saved:
        ym = f[9:15]; key_m = f"{ym[:4]}-{ym[4:]}"
        month = {"weekday": {}, "weekend": {}}
        for row in csv.DictReader(open(f"{RAW}/{f}", newline="", encoding="utf-8-sig")):
            day = "weekday" if row["DAY_TYPE"].strip().upper() == "WEEKDAY" else "weekend"
            key = "/".join(c for c in re.split(r"[-/ ]+", row["PT_CODE"].strip()) if c)
            rec = month[day].setdefault(key, [[0] * 24, [0] * 24])
            h = int(row["TIME_PER_HOUR"])
            rec[0][h] += int(row["TOTAL_TAP_IN_VOLUME"]); rec[1][h] += int(row["TOTAL_TAP_OUT_VOLUME"])
        hist["months"].append(key_m); hist["days"][key_m] = day_counts(ym); hist["data"][key_m] = month
    json.dump(hist, open(f"{OUT}/pv_history.json", "w"), separators=(",", ":"))
    print(f"pv_history.json: {len(saved)} months archived ({hist['months'][0]} to {hist['months'][-1]})")


def build_hourly(raw_path, ym):
    out = {"month": ym, "source": "LTA DataMall PV/Train", "weekday": {}, "weekend": {}}
    for row in csv.DictReader(open(raw_path, newline="", encoding="utf-8-sig")):
        day = "weekday" if row["DAY_TYPE"].strip().upper() == "WEEKDAY" else "weekend"
        hour = int(row["TIME_PER_HOUR"])
        tin, tout = int(row["TOTAL_TAP_IN_VOLUME"]), int(row["TOTAL_TAP_OUT_VOLUME"])
        codes = [c for c in re.split(r"[-/ ]+", row["PT_CODE"].strip()) if c]
        # an interchange reports one combined figure; store it under every code it carries
        key = "/".join(codes)
        rec = out[day].setdefault(key, {"in": [0] * 24, "out": [0] * 24})
        rec["in"][hour] += tin; rec["out"][hour] += tout
    json.dump(out, open(f"{OUT}/station_hourly.json", "w"), separators=(",", ":"))
    print(f"station_hourly.json: {len(out['weekday'])} stations ({ym})")


def crowd(key):
    now = dt.datetime.now(SGT)
    forecast, live = {"fetched_at": now.isoformat(), "stations": {}}, {"fetched_at": now.isoformat(), "stations": {}}
    for line in LINES:
        try:
            for day in get("PCDForecast", key, TrainLine=line).get("value", []):
                forecast["date"] = day.get("Date", "")[:10]
                for st in day.get("Stations", []):
                    forecast["stations"][st["Station"]] = [[iv["Start"][11:16], iv["CrowdLevel"]] for iv in st.get("Interval", [])]
        except Exception as e:
            print(f"PCDForecast {line}: {e}")
        try:
            for st in get("PCDRealTime", key, TrainLine=line).get("value", []):
                live["stations"][st["Station"]] = st["CrowdLevel"]
        except Exception as e:
            print(f"PCDRealTime {line}: {e}")
    json.dump(forecast, open(f"{OUT}/crowd_forecast.json", "w"), separators=(",", ":"))
    json.dump(live, open(f"{OUT}/crowd_now.json", "w"), separators=(",", ":"))
    os.makedirs(RAW, exist_ok=True)
    json.dump(forecast, open(f"{RAW}/crowd_forecast_{now:%Y%m%d}.json", "w"), separators=(",", ":"))
    print(f"crowd: forecast for {len(forecast['stations'])} stations, live for {len(live['stations'])}")


if __name__ == "__main__":
    key = os.environ.get("LTA_ACCOUNT_KEY")
    if not key:
        sys.exit("LTA_ACCOUNT_KEY not set - add it as a repo secret")
    os.makedirs(OUT, exist_ok=True)
    passenger_volume(key)
    crowd(key)
