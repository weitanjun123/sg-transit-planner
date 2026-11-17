"""Early wakers, night owls and point-to-point flows -> docs/data/report.json (rendered by docs/report.html).

Uses only the archived LTA files (no API calls):
  data/raw/lta/pv_train_YYYYMM.csv          tap-ins / tap-outs per station per hour
  data/archive/lta/train_od/YYYYMM.zip      trips per origin -> destination station per hour
All archived months are pooled and turned into per-day averages (weekdays / weekends counted from the calendar).
"""
import csv, glob, io, json, math, os, re, zipfile, datetime as dt
from collections import defaultdict
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SGT = dt.timezone(dt.timedelta(hours=8))
MIN_DAILY = 2000          # stations with fewer weekday tap-ins per day are left out of the rankings
AM_HOURS = (6, 7, 8)      # morning commute used to find where each station's riders are going


def day_counts(ym):
    from sgdays import day_counts as dc
    c = dc(ym)
    return {"WEEKDAY": c["weekday"], "WEEKEND": c["weekend"]}


def daytype(s):
    return "WEEKDAY" if s.strip().upper() == "WEEKDAY" else "WEEKEND"


def km(a, b):
    return math.hypot((a["lon"] - b["lon"]) * 111.32 * math.cos(math.radians(1.35)), (a["lat"] - b["lat"]) * 110.57)


def spearman(x, y):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for k, i in enumerate(o): r[i] = k
        return r
    return pearson(rank(x), rank(y))


def pearson(x, y):
    n = len(x); mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y)); sxx = sum((a - mx) ** 2 for a in x); syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0


def main():
    st = json.load(open("docs/data/stations.json"))
    code2st = {c["code"]: s for s in st["stations"] for c in s["codes"]}

    def station(key):
        for c in re.split(r"[-/ ]+", key.strip()):
            if c in code2st:
                return code2st[c]
        return None

    # ---- tap-ins / tap-outs ------------------------------------------------------------------------
    pv_files = sorted(glob.glob("data/raw/lta/pv_train_*.csv"))
    months = [re.search(r"(\d{6})", f).group(1) for f in pv_files]
    days = {"WEEKDAY": 0, "WEEKEND": 0}
    for ym in months:
        for k, v in day_counts(ym).items(): days[k] += v
    tap = defaultdict(lambda: {"WEEKDAY": [[0] * 24, [0] * 24], "WEEKEND": [[0] * 24, [0] * 24]})
    for f in pv_files:
        for r in csv.DictReader(open(f, newline="", encoding="utf-8-sig")):
            s = station(r["PT_CODE"])
            if not s:
                continue
            h = int(r["TIME_PER_HOUR"]); rec = tap[s["id"]][daytype(r["DAY_TYPE"])]
            rec[0][h] += int(r["TOTAL_TAP_IN_VOLUME"]); rec[1][h] += int(r["TOTAL_TAP_OUT_VOLUME"])
    byid = {s["id"]: s for s in st["stations"]}

    # ---- origin -> destination trips ---------------------------------------------------------------
    od_files = sorted(glob.glob("data/archive/lta/train_od/*.zip"))
    od = defaultdict(int)          # (daytype, hour, origin_id, dest_id) -> trips (summed over months)
    for f in od_files:
        z = zipfile.ZipFile(f)
        name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        cache = {}
        for r in csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig")):
            o, d = r["ORIGIN_PT_CODE"], r["DESTINATION_PT_CODE"]
            so = cache.get(o) or cache.setdefault(o, station(o)); sd = cache.get(d) or cache.setdefault(d, station(d))
            if so and sd:
                od[(daytype(r["DAY_TYPE"]), int(r["TIME_PER_HOUR"]), so["id"], sd["id"])] += int(r["TOTAL_TRIPS"])

    # ---- per-station metrics -----------------------------------------------------------------------
    rows = []
    am_dist = defaultdict(lambda: [0.0, 0])   # origin -> [trip-weighted km, trips] in the AM commute
    for (dtp, h, o, d), n in od.items():
        if dtp == "WEEKDAY" and h in AM_HOURS and o != d:
            am_dist[o][0] += n * km(byid[o], byid[d]); am_dist[o][1] += n
    am_dest = defaultdict(int)
    for (dtp, h, o, d), n in od.items():
        if dtp == "WEEKDAY" and h in AM_HOURS and o != d:
            am_dest[d] += n
    for sid, rec in tap.items():
        s = byid[sid]
        out = {"name": s["name"], "codes": "/".join(c["code"] for c in s["codes"] if c["open_year"] == 0), "lat": s["lat"], "lon": s["lon"]}
        for dtp in ("WEEKDAY", "WEEKEND"):
            tin, tout = rec[dtp]; n = days[dtp] or 1
            total_in, total_out = sum(tin), sum(tout)
            morning = sum(tin[h] for h in range(4, 12))
            out[dtp.lower()] = {
                "in_per_day": round(total_in / n), "out_per_day": round(total_out / n),
                "in_before6_pct": round(100 * tin[5] / total_in, 2) if total_in else 0,
                "in_before7_pct": round(100 * (tin[5] + tin[6]) / total_in, 2) if total_in else 0,
                "in_5am_per_day": round(tin[5] / n),
                "am_rush_mid": round(sum((h + .5) * tin[h] for h in range(4, 12)) / morning, 2) if morning else None,
                "out_after23_pct": round(100 * (tout[23] + tout[0]) / total_out, 2) if total_out else 0,
                "out_after23_per_day": round((tout[23] + tout[0]) / n),
                "in_after21_per_day": round((tin[21] + tin[22] + tin[23]) / n),
                "in_after21_pct": round(100 * (tin[21] + tin[22] + tin[23]) / total_in, 2) if total_in else 0,
                "hourly_in": [round(v / n) for v in tin], "hourly_out": [round(v / n) for v in tout],
            }
        dkm = am_dist.get(sid)
        out["am_trip_km"] = round(dkm[0] / dkm[1], 2) if dkm and dkm[1] else None
        out["am_trips_out_per_day"] = round(dkm[1] / days["WEEKDAY"]) if dkm else 0
        out["am_arrivals_per_day"] = round(am_dest.get(sid, 0) / days["WEEKDAY"])
        rows.append(out)

    ranked = [r for r in rows if r["weekday"]["in_per_day"] >= MIN_DAILY]

    # ---- does distance explain early waking? -------------------------------------------------------
    # only "home" stations (more people leave than arrive 7-9am); CBD/work stations are destinations, not starting points
    def homeish(r):
        w = r["weekday"]; tin = sum(w["hourly_in"][7:9]); tout = sum(w["hourly_out"][7:9])
        return tin >= 1.5 * max(tout, 1)
    pts = [r for r in ranked if r["am_trip_km"] and r["weekday"]["am_rush_mid"] and homeish(r)]
    x = [r["am_trip_km"] for r in pts]; y1 = [r["weekday"]["in_before7_pct"] for r in pts]; y2 = [r["weekday"]["am_rush_mid"] for r in pts]
    n = len(x); mx = sum(x) / n; my = sum(y2) / n
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y2)) / sum((a - mx) ** 2 for a in x)
    icpt = my - slope * mx
    for r in pts:
        r["rush_mid_expected"] = round(icpt + slope * r["am_trip_km"], 2)
        r["minutes_vs_expected"] = round((r["weekday"]["am_rush_mid"] - r["rush_mid_expected"]) * 60, 1)
    mx1, my1 = mx, sum(y1) / n
    slope1 = sum((a - mx1) * (b - my1) for a, b in zip(x, y1)) / sum((a - mx1) ** 2 for a in x)
    top_id = max(am_dest, key=am_dest.get); top = byid[top_id]
    for r in pts:
        r["km_to_top"] = round(km(r, top), 2)
    xt = [r["km_to_top"] for r in pts]
    mt = sum(xt) / n; slope_t = sum((a - mt) * (b - my) for a, b in zip(xt, y2)) / sum((a - mt) ** 2 for a in xt)
    it = my - slope_t * mt
    for r in pts:
        r["minutes_vs_top"] = round((r["weekday"]["am_rush_mid"] - (it + slope_t * r["km_to_top"])) * 60, 1)
    dist_top = {"station": top["name"], "pearson_before7": round(pearson(xt, y1), 2), "spearman_before7": round(spearman(xt, y1), 2),
                "pearson_rush_mid": round(pearson(xt, y2), 2), "spearman_rush_mid": round(spearman(xt, y2), 2),
                "minutes_earlier_per_km": round(-slope_t * 60, 2)}
    dist = {"stations": n, "to_top_destination": dist_top, "excluded_work_stations": len([r for r in ranked if not homeish(r)]), "pearson_before7": round(pearson(x, y1), 2), "spearman_before7": round(spearman(x, y1), 2),
            "pearson_rush_mid": round(pearson(x, y2), 2), "spearman_rush_mid": round(spearman(x, y2), 2),
            "minutes_earlier_per_km": round(-slope * 60, 2), "before7_pts_per_km": round(slope1, 2),
            "earlier_than_distance_suggests": [{"name": r["name"], "minutes": r["minutes_vs_top"], "km": r["km_to_top"]} for r in sorted(pts, key=lambda r: r["minutes_vs_top"])[:8]],
            "later_than_distance_suggests": [{"name": r["name"], "minutes": r["minutes_vs_top"], "km": r["km_to_top"]} for r in sorted(pts, key=lambda r: -r["minutes_vs_top"])[:8]],
            "scatter": [[r["name"], r["am_trip_km"], r["weekday"]["in_before7_pct"], r["weekday"]["am_rush_mid"], r["km_to_top"], r["weekday"]["in_per_day"]] for r in pts]}

    # ---- point-to-point flows -----------------------------------------------------------------------
    def top_pairs(dtp, hours, k=15):
        agg = defaultdict(int)
        for (d_, h, o, d), v in od.items():
            if d_ == dtp and h in hours and o != d:
                agg[(o, d)] += v
        return [{"from": byid[o]["name"], "to": byid[d]["name"], "trips_per_day": round(v / days[dtp]), "km": round(km(byid[o], byid[d]), 1),
                 "return_trips_per_day": round(sum(od.get((dtp, h, d, o), 0) for h in range(24)) / days[dtp])}
                for (o, d), v in sorted(agg.items(), key=lambda kv: -kv[1])[:k]]

    def late_dest(dtp, hours, k=12):
        agg = defaultdict(int); src = defaultdict(lambda: defaultdict(int))
        for (d_, h, o, d), v in od.items():
            if d_ == dtp and h in hours and o != d:
                agg[d] += v; src[d][o] += v
        return [{"to": byid[d]["name"], "trips_per_day": round(v / days[dtp]),
                 "mostly_from": [byid[o]["name"] for o, _ in sorted(src[d].items(), key=lambda kv: -kv[1])[:3]]}
                for d, v in sorted(agg.items(), key=lambda kv: -kv[1])[:k]]

    def lst(key, field, k=15, minv=None):
        pool = [r for r in ranked if (minv is None or r["weekday"].get(minv[0], 0) >= minv[1])]
        return [{"name": r["name"], "codes": r["codes"], field: r["weekday"][field], "in_per_day": r["weekday"]["in_per_day"],
                 "am_trip_km": r["am_trip_km"]} for r in sorted(pool, key=lambda r: -r["weekday"][field])[:k]]

    rep = {
        "generated": dt.datetime.now(SGT).isoformat(timespec="minutes"),
        "months": [f"{m[:4]}-{m[4:]}" for m in months], "od_months": [os.path.basename(f)[:6] for f in od_files], "days": days,
        "min_daily": MIN_DAILY,
        "early_share": lst("early", "in_before7_pct"),
        "early_5am": lst("early", "in_5am_per_day"),
        "earliest_rush": [{"name": r["name"], "codes": r["codes"], "am_rush_mid": r["weekday"]["am_rush_mid"], "am_trip_km": r["am_trip_km"]}
                          for r in sorted([r for r in ranked if r["weekday"]["am_rush_mid"]], key=lambda r: r["weekday"]["am_rush_mid"])[:15]],
        "latest_rush": [{"name": r["name"], "codes": r["codes"], "am_rush_mid": r["weekday"]["am_rush_mid"], "am_trip_km": r["am_trip_km"]}
                        for r in sorted([r for r in ranked if r["weekday"]["am_rush_mid"]], key=lambda r: -r["weekday"]["am_rush_mid"])[:10]],
        "late_share": lst("late", "out_after23_pct"),
        "late_count": lst("late", "out_after23_per_day"),
        "night_in": lst("night", "in_after21_per_day"),
        "am_destinations": [{"name": byid[d]["name"], "arrivals_per_day": round(v / days["WEEKDAY"])} for d, v in sorted(am_dest.items(), key=lambda kv: -kv[1])[:10]],
        "distance": dist,
        "pairs_am": top_pairs("WEEKDAY", (7, 8)),
        "pairs_night": top_pairs("WEEKDAY", (22, 23, 0)),
        "late_home": late_dest("WEEKDAY", (23, 0)),
        "stations": rows,
    }
    json.dump(rep, open("docs/data/report.json", "w"), separators=(",", ":"))
    print(f"report.json: {len(rows)} stations, {len(ranked)} ranked, months {rep['months']}, OD {rep['od_months']}; "
          f"distance vs before-7am share: spearman {dist['spearman_before7']}, {dist['minutes_earlier_per_km']} min earlier per km")


if __name__ == "__main__":
    main()
