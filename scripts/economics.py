"""Does it make financial sense? -> docs/data/economics.json (rendered by docs/economics.html)

 1. Cost vs benefit of every proposed line / single station (time saved, buses freed, land value that could be captured)
 3. More trains on the most crowded stretches: how many, what they cost
 4. Bus-to-MRT hotspots: infill station vs a direct express bus
 6. Near-empty trains: where running fewer trains saves money for little extra waiting
 8. One more hour of trains on Friday and Saturday nights: riders vs running cost

All money assumptions are in data/manual/costs.json with their sources (or marked as assumptions). Inputs come from the
other scripts' outputs, so no API calls.
"""
import json, math, os, re, datetime as dt
from collections import defaultdict

SGT = dt.timezone(dt.timedelta(hours=8))
D = "docs/data"
LINES = {"NS": {"cap": 1920, "cars": 6, "peak_tph": 27}, "EW": {"cap": 1920, "cars": 6, "peak_tph": 27}, "NE": {"cap": 1920, "cars": 6, "peak_tph": 24},
         "CC": {"cap": 930, "cars": 3, "peak_tph": 24}, "DT": {"cap": 930, "cars": 3, "peak_tph": 24}, "TE": {"cap": 1280, "cars": 4, "peak_tph": 20}}


def load(p, d=None):
    return json.load(open(p)) if os.path.exists(p) else d


def km(a, b):
    return math.hypot((a[1] - b[1]) * 111.32 * math.cos(math.radians(1.35)), (a[0] - b[0]) * 110.57)


def offpeak_tph(h, weekend=False):
    hw = (7 if h < 6 else 6 if h < 9 else 4.5 if h < 22 else 6) if weekend else \
         (7 if h < 6 else 3.5 if h < 7 else 0 if h < 9 else 4.5 if h < 17 else 0 if h < 20 else 5.5 if h < 23 else 7)
    return 60 / hw if hw else None


def main():
    C = {k: v["value"] for k, v in json.load(open("data/manual/costs.json")).items() if not k.startswith("_")}
    VOT = C["median_monthly_income"] / C["work_hours_per_month"] * C["value_of_time_share"]
    r, n = C["discount_rate"], C["appraisal_years"]
    ANN = (1 - (1 + r) ** -n) / r
    DAYS = C["weekdays_per_year"] + C["weekend_factor"] * (365 - C["weekdays_per_year"])
    st = load(f"{D}/stations.json"); an = load(f"{D}/analysis.json", {}); ins = load(f"{D}/insights.json", {}); px = load(f"{D}/prices.json", {})
    seg = load(f"{D}/segment_loads.json", {}); pv = load(f"{D}/station_hourly.json", {})
    blocks = load(f"{D}/hdb_blocks.json", [])
    now_year = dt.date.today().year + dt.date.today().timetuple().tm_yday / 366
    S = {s["id"]: s for s in st["stations"]}

    # line geometry: open stops in code order, run time end to end (40 km/h + 0.5 min a stop)
    geo = {}
    for k in LINES:
        stops = sorted(((int(re.sub(r"\D", "", c["code"]) or 0), c["code"], s) for s in st["stations"] for c in s["codes"]
                        if c["open_year"] == 0 and re.match(r"[A-Z]+", c["code"]).group(0) == k), key=lambda t: t[0])
        run = sum(km((a[2]["lat"], a[2]["lon"]), (b[2]["lat"], b[2]["lon"])) / 40 * 60 + 0.5 for a, b in zip(stops, stops[1:]))
        geo[k] = {"stops": [(c, s) for _, c, s in stops], "round_trip_h": (2 * run + 10) / 60}

    # ------------------------------------------------------------ 1: proposed lines + single stations
    px_blocks = {(round(b[0], 5), round(b[1], 5)): b for b in (px.get("hdb") or {}).get("blocks", [])}
    feeders = (ins.get("bus") or {}).get("areas", [])
    def appraise(name, sites, cost, kind, residents):
        trips = residents * C["rail_share_new_residents"] * 2
        time_yr = trips * C["minutes_saved_new_station"] / 60 * VOT * DAYS
        fd = sum(a["feeder_trips_per_day"] for a in feeders if any(km((a["lat"], a["lon"]), (p["lat"], p["lon"])) < 0.8 for p in sites))
        buses = fd / 3 / (60 * 2)                       # 3 peak hours, ~60 riders a trip, 2 trips an hour per bus
        bus_yr = buses * C["bus_cost_per_year"]
        units = val = 0
        for lat, lon, u, yr, town in blocks:
            if any(km((lat, lon), (p["lat"], p["lon"])) < 0.4 for p in sites):
                units += u
                b = px_blocks.get((round(lat, 5), round(lon, 5)))
                if b: val += u * b[7]
        uplift = val * C["price_uplift_near_station"]
        pv_benefit = (time_yr + bus_yr) * ANN
        return {"name": name, "kind": kind, "cost": round(cost), "new_residents": round(residents), "daily_trips": round(trips),
                "time_saved_per_year": round(time_yr), "buses_freed": round(buses, 1), "bus_saving_per_year": round(bus_yr),
                "pv_benefits": round(pv_benefit), "bcr": round(pv_benefit / cost, 2) if cost else None,
                "fare_revenue_per_year": round(trips * C["avg_fare"] * DAYS),
                "hdb_units_within_400m": units, "land_value_uplift": round(uplift), "uplift_share_of_cost": round(uplift / cost, 2) if cost else None,
                "cost_per_daily_trip": round(cost / trips) if trips else None}
    proj = []
    for l in an.get("lines", []):
        sites = [p for p in l["stations"] if p.get("site")]
        proj.append(appraise(l["name"], sites, l["length_km"] * C["new_line_cost_per_km"], "new line", l["new_residents"]) | {"length_km": l["length_km"], "stations": len(sites)})
    name2s = {x["name"]: x for x in st["stations"]}
    for s in an.get("infill", []):
        if s.get("type") == "infill":      # sits on an existing line: station only
            cost, kind, L = C["infill_station_cost"], f"infill station on {s.get('line')}", 0
        else:                               # needs a short spur of new track to the nearest station
            t = name2s.get((s.get("between") or [None])[0])
            L = round(km((s["lat"], s["lon"]), (t["lat"], t["lon"])), 1) if t else 1.0
            cost, kind = C["infill_station_cost"] + L * C["new_line_cost_per_km"], f"new station + {L} km spur from {t['name'] if t else 'nearest station'}"
        proj.append(appraise(f"Station near {s['area']}", [s], cost, kind, s["new_residents"]) | {"length_km": L, "stations": 1})
    proj.sort(key=lambda p: -(p["bcr"] or 0))

    # ------------------------------------------------------------ 3: more trains on crowded stretches
    trains = []
    wk = seg.get("weekday", {})
    for k, g in LINES.items():
        worst = (0, None, None)
        for key, arr in wk.items():
            if re.match(r"[A-Z]+", key).group(0) != k:
                continue
            for h in (7, 8, 17, 18, 19):
                if arr[h] > worst[0]:
                    worst = (arr[h], key, h)
        if not worst[1]:
            continue
        load_h, key, h = worst
        a, b = key.split(">"); code2 = {c["code"]: s for s in st["stations"] for c in s["codes"]}
        cap_h = g["peak_tph"] * g["cap"]; peak15 = load_h * C["peak_15min_factor"]
        need_tph = peak15 / (C["target_load"] * g["cap"])
        extra_tph = max(0.0, need_tph - g["peak_tph"]); capped = need_tph > C["max_trains_per_hour"]
        extra_tph = min(extra_tph, C["max_trains_per_hour"] - g["peak_tph"])
        extra_trains = math.ceil(extra_tph * geo[k]["round_trip_h"]) if extra_tph > 0 else 0
        price = C["train_cost_6car"] * g["cars"] / 6
        opex = extra_trains * 4 * C["train_cost_per_train_hour"] * C["weekdays_per_year"]   # ~4 peak hours a weekday
        trains.append({"line": st["lines"][k]["name"], "worst_stretch": f"{code2[a]['name']} → {code2[b]['name']}", "hour": f"{h}:00",
                       "people_per_hour": round(load_h), "busiest_15min_pct": round(100 * peak15 / cap_h), "trains_per_hour_now": g["peak_tph"],
                       "trains_per_hour_needed": round(need_tph, 1), "extra_trains": extra_trains, "capex": round(extra_trains * price),
                       "opex_per_year": round(opex), "hits_signalling_limit": capped,
                       "cost_per_rider_relieved_per_year": round((extra_trains * price / ANN + opex) / (load_h * 2 * C["weekdays_per_year"])) if extra_trains else 0})
    trains.sort(key=lambda t: -t["busiest_15min_pct"])

    # ------------------------------------------------------------ 4: bus-to-MRT hotspots: infill station vs express bus
    open_edges = [e for e in st["edges"] if e["open_year"] == 0 and e["until"] is None and st["lines"].get(e["line"], {}).get("mode") == "MRT"]
    def seg_dist(p, a, b):
        ax, ay = (a[1] - p[1]) * 111.32, (a[0] - p[0]) * 110.57; bx, by = (b[1] - p[1]) * 111.32, (b[0] - p[0]) * 110.57
        dx, dy = bx - ax, by - ay; t = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0
        return math.hypot(ax + t * dx, ay + t * dy), t
    covered_by = {}
    for l in an.get("lines", []):
        for p in l["stations"]:
            if p.get("site"):
                covered_by[(p["lat"], p["lon"])] = l["name"]
    hot = []
    for a in feeders[:15]:
        P = (a["lat"], a["lon"]); trips = a["feeder_trips_per_day"] * 2          # morning trip + the trip home
        best = None
        for e in open_edges:
            A, B = S[e["a"]], S[e["b"]]
            d, t = seg_dist(P, (A["lat"], A["lon"]), (B["lat"], B["lon"]))
            if 0.2 < t < 0.8 and (best is None or d < best[0]):
                best = (d, e, A, B)
        infill_ok = best and best[0] <= 0.8 and min(km(P, (S[x]["lat"], S[x]["lon"])) for x in (best[1]["a"], best[1]["b"])) > 0.9
        inf_benefit = trips * (C["bus_feeder_minutes"] - 5) / 60 * VOT * DAYS
        inf_bus = a["feeder_trips_per_day"] / 3 / 120 * C["bus_cost_per_year"]
        inf_npv = (inf_benefit + inf_bus) * ANN - C["infill_station_cost"]
        exp_buses = math.ceil(a["feeder_trips_per_day"] / 3 / (80 * 2))
        exp_benefit = trips * 4 / 60 * VOT * DAYS
        exp_npv = (exp_benefit - exp_buses * C["bus_cost_per_year"]) * ANN
        cov = next((nm for (la, lo), nm in covered_by.items() if km(P, (la, lo)) < 0.8), None)
        hot.append({"town": a["town"] or "area", "lat": a["lat"], "lon": a["lon"], "feeder_trips_per_day": a["feeder_trips_per_day"], "km_to_mrt": a["km_to_mrt"],
                    "infill_possible": bool(infill_ok), "infill_between": [best[2]["name"], best[3]["name"]] if infill_ok else None,
                    "infill_npv": round(inf_npv) if infill_ok else None, "express_bus_buses": exp_buses, "express_bus_npv": round(exp_npv),
                    "covered_by_proposed_line": cov,
                    "verdict": ("Covered by " + cov) if cov else ("Infill station" if infill_ok and inf_npv > max(exp_npv, 0) else "Express bus" if exp_npv > 0 else "Neither pays off")})

    # ------------------------------------------------------------ 6: near-empty trains
    lean = []
    for k, g in LINES.items():
        for weekend in (False, True):
            arrs = [(key, arr) for key, arr in (seg.get("weekend" if weekend else "weekday", {})).items() if re.match(r"[A-Z]+", key).group(0) == k]
            for h in list(range(9, 24)) + [6]:
                tph = offpeak_tph(h, weekend)
                if not tph or not arrs:
                    continue
                mx = max(arr[h] for _, arr in arrs); load_pct = mx / (tph * g["cap"])
                if load_pct >= 0.25:
                    continue
                new_tph = max(60 / 8, mx / (0.4 * g["cap"]))          # aim for 40% full, never worse than 8 minutes
                if new_tph >= tph - 0.5:
                    continue
                saved_train_h = (tph - new_tph) * geo[k]["round_trip_h"]
                days = C["weekdays_per_year"] if not weekend else 365 - C["weekdays_per_year"]
                saving = saved_train_h * C["train_cost_per_train_hour"] * days
                riders = sum(arr[h] for _, arr in arrs) / max(1, len(geo[k]["stops"]) / 3)   # rough boardings on the line that hour
                extra_wait = (60 / new_tph - 60 / tph) / 2
                wait_cost = riders * extra_wait / 60 * VOT * days
                lean.append({"line": st["lines"][k]["name"], "day": "weekend" if weekend else "weekday", "hour": f"{h}:00",
                             "fullest_train_pct_now": round(100 * load_pct), "minutes_between_trains_now": round(60 / tph, 1),
                             "minutes_between_trains_suggested": round(60 / new_tph, 1), "saving_per_year": round(saving),
                             "riders_extra_wait_cost_per_year": round(wait_cost), "net_per_year": round(saving - wait_cost)})
    lean = sorted([x for x in lean if x["net_per_year"] > 0], key=lambda x: -x["net_per_year"])
    lean_sum = defaultdict(lambda: {"slots": 0, "net_per_year": 0, "hours": []})
    for x in lean:
        k_ = (x["line"], x["day"]); lean_sum[k_]["slots"] += 1; lean_sum[k_]["net_per_year"] += x["net_per_year"]; lean_sum[k_]["hours"].append(x["hour"])
    lean_lines = sorted([{"line": a, "day": b, **v} for (a, b), v in lean_sum.items()], key=lambda x: -x["net_per_year"])

    # ------------------------------------------------------------ 8: one more hour of trains on Fri/Sat nights
    late = None
    if pv:
        we = pv.get("weekend", {}); days_we = (pv.get("days") or {}).get("weekend", 9)
        h23 = sum(r["in"][23] for r in we.values()) / days_we; h0 = sum(r["in"][0] for r in we.values()) / days_we
        extra = h0 * (h0 / h23) if h23 else 0
        trains_needed = sum(math.ceil(60 / 7 * g["round_trip_h"]) for g in geo.values())
        cost_night = trains_needed * C["train_cost_per_train_hour"] * 1.0
        nights = 104
        out0 = {k_: r_["out"][0] for k_, r_ in we.items()}
        ns_share = sum(v for k_, v in out0.items() if any(c.startswith("NS") for c in k_.split("/"))) / (sum(out0.values()) or 1)
        ns_trains = math.ceil(60 / 7 * geo["NS"]["round_trip_h"]); ns_riders = extra * ns_share; ns_cost = ns_trains * C["train_cost_per_train_hour"]
        ns_only = {"riders": round(ns_riders), "trains": ns_trains, "cost_per_night": round(ns_cost), "bcr": round(ns_riders * C["taxi_late_fare"] / ns_cost, 2) if ns_cost else None,
                   "share_of_late_riders": round(100 * ns_share)}
        late = {"tap_ins_23h_weekend": round(h23), "tap_ins_0h_weekend": round(h0), "estimated_riders_extra_hour": round(extra),
                "trains_in_service": trains_needed, "running_cost_per_night": round(cost_night), "cost_per_extra_rider": round(cost_night / extra, 1) if extra else None,
                "taxi_alternative": C["taxi_late_fare"], "benefit_per_night": round(extra * C["taxi_late_fare"]),
                "bcr": round(extra * C["taxi_late_fare"] / cost_night, 2) if cost_night else None, "nights_per_year": nights,
                "net_per_year": round((extra * C["taxi_late_fare"] - cost_night) * nights),
                "top_late_destinations": [d["to"] for d in (load(f"{D}/report.json", {}) or {}).get("late_home", [])[:6]],
                "north_south_only": ns_only,
                "caveat": "Engineering hours: the network needs a nightly maintenance window, so a later last train shortens it. MOT has cited this and low past demand."}

    cfg = json.load(open("data/manual/costs.json"))
    out = {"generated": dt.datetime.now(SGT).isoformat(timespec="minutes"), "value_of_time_per_hour": round(VOT, 2), "annuity_factor": round(ANN, 2),
           "equivalent_days_per_year": round(DAYS), "assumptions": {k: v for k, v in cfg.items() if not k.startswith("_")},
           "projects": proj, "trains": trains, "hotspots": hot, "lean": lean[:20], "lean_lines": lean_lines, "late_night": late}
    json.dump(out, open(f"{D}/economics.json", "w"), separators=(",", ":"))
    print(f"economics.json: {len(proj)} projects, {len(trains)} lines, {len(hot)} hotspots, {len(lean)} lean slots")


if __name__ == "__main__":
    main()
