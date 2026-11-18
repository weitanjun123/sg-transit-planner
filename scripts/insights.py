"""Deeper reads of the archived LTA data -> docs/data/insights.json, docs/data/segment_loads.json, docs/data/heartbeat.json

 1. Hidden transfer load    every train trip routed on the rail network; who changes lines where
 2. Bus-to-MRT dependence   morning bus trips that end next to a station (feeder trips), by where they start
 3. Commute minutes         estimated minutes on the train per trip, by home station and HDB town
 4. Station personalities   stations grouped by the shape of their 24 hours (k-means)
 5. Weekend vs weekday      which stations come alive on weekends
 6. Morning vs evening      morning trips with no matching MRT trip home
 7. 24-hour heartbeat       top station-to-station flows for every hour (for the animation page)

Inputs (all archived in the repo, no API calls): data/raw/lta/pv_train_*.csv, data/archive/lta/train_od/*.zip,
data/archive/lta/bus_od/*.zip, data/raw/lta/bus_stops.json, docs/data/stations.json, docs/data/hdb_blocks.json
"""
import csv, glob, heapq, io, json, math, os, re, sys, zipfile, datetime as dt
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sgdays import day_counts

SGT = dt.timezone(dt.timedelta(hours=8))
SPEED = {"MRT": 40.0, "LRT": 25.0}   # average km/h incl. acceleration (straight-line distance between stations)
DWELL, TRANSFER_MIN = 0.5, 5.0       # minutes per stop, minutes to change lines (walk + wait)
MRT_LINES = {"NS", "EW", "CG", "NE", "CC", "DT", "TE"}


def km(a, b):
    return math.hypot((a[1] - b[1]) * 111.32 * math.cos(math.radians(1.35)), (a[0] - b[0]) * 110.57)


def dtype(s):
    return "weekday" if s.strip().upper() == "WEEKDAY" else "weekend"


def kmeans(X, k, iters=60):
    # deterministic farthest-point init
    mean = [sum(c) / len(X) for c in zip(*X)]
    d2 = lambda a, b: sum((p - q) ** 2 for p, q in zip(a, b))
    C = [max(X, key=lambda x: d2(x, mean))]
    while len(C) < k:
        C.append(max(X, key=lambda x: min(d2(x, c) for c in C)))
    for _ in range(iters):
        lab = [min(range(k), key=lambda j: d2(x, C[j])) for x in X]
        newC = []
        for j in range(k):
            m = [x for x, l in zip(X, lab) if l == j]
            newC.append([sum(c) / len(m) for c in zip(*m)] if m else C[j])
        if newC == C:
            break
        C = newC
    return lab, C


def main():
    now_year = dt.date.today().year + dt.date.today().timetuple().tm_yday / 366
    st = json.load(open("docs/data/stations.json"))
    S = {s["id"]: s for s in st["stations"]}
    pos = {sid: (s["lat"], s["lon"]) for sid, s in S.items()}
    code2sid = {c["code"]: s["id"] for s in st["stations"] for c in s["codes"]}
    open_now = {sid for sid, s in S.items() if s["open_year"] <= now_year}

    def sid_of(key):
        for c in re.split(r"[-/ ]+", key.strip()):
            if c in code2sid and code2sid[c] in open_now:
                return code2sid[c]
        return None

    # ---------------------------------------------------------------- rail graph (today's network)
    adj = defaultdict(list)
    code_on = {}  # (sid, line) -> station code on that line
    for e in st["edges"]:
        if e["open_year"] > now_year or (e["until"] is not None and now_year >= e["until"]):
            continue
        c1, c2 = e["codes"]
        line = e["line"]
        if line in ("STC", "PTC"):
            line = re.match(r"[A-Z]+", c2).group(0)
        mode = st["lines"].get(line, {}).get("mode", "MRT")
        a, b = (e["a"], line), (e["b"], line)
        code_on[a] = c1; code_on[b] = c2
        t = km(pos[e["a"]], pos[e["b"]]) / SPEED[mode] * 60 + DWELL
        adj[a].append((b, t)); adj[b].append((a, t))
    by_station = defaultdict(list)
    for n in list(adj):
        by_station[n[0]].append(n)
    for sid, nodes in by_station.items():
        for x in nodes:
            for y in nodes:
                if x != y:
                    adj[x].append((y, TRANSFER_MIN))

    path_cache = {}

    def routes_from(o):
        dist, prev, pq = {}, {}, []
        for n in by_station.get(o, []):
            dist[n] = 0.0; heapq.heappush(pq, (0.0, n))
        while pq:
            d, n = heapq.heappop(pq)
            if d > dist.get(n, 1e18):
                continue
            for m, w in adj[n]:
                nd = d + w
                if nd < dist.get(m, 1e18) - 1e-9:
                    dist[m] = nd; prev[m] = n; heapq.heappush(pq, (nd, m))
        out = {}
        for dsid, nodes in by_station.items():
            best = min(nodes, key=lambda n: dist.get(n, 1e18))
            if dist.get(best, 1e18) >= 1e18:
                continue
            p = [best]
            while p[-1] in prev:
                p.append(prev[p[-1]])
            p.reverse()
            # drop leading transfer moves at the origin
            while len(p) > 1 and p[0][0] == p[1][0]:
                p.pop(0)
            out[dsid] = (dist[best], p)
        return out

    # ---------------------------------------------------------------- train OD (pooled months)
    od_files = sorted(glob.glob("data/archive/lta/train_od/*.zip"))
    months = [os.path.basename(f)[:6] for f in od_files]
    days = {"weekday": 0, "weekend": 0}
    for m in months:
        c = day_counts(m); days["weekday"] += c["weekday"]; days["weekend"] += c["weekend"]
    od = defaultdict(float)
    for f in od_files:
        z = zipfile.ZipFile(f); name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        cache = {}
        for r in csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig")):
            o = cache[r["ORIGIN_PT_CODE"]] if r["ORIGIN_PT_CODE"] in cache else cache.setdefault(r["ORIGIN_PT_CODE"], sid_of(r["ORIGIN_PT_CODE"]))
            d = cache[r["DESTINATION_PT_CODE"]] if r["DESTINATION_PT_CODE"] in cache else cache.setdefault(r["DESTINATION_PT_CODE"], sid_of(r["DESTINATION_PT_CODE"]))
            if o is not None and d is not None and o != d:
                od[(dtype(r["DAY_TYPE"]), int(r["TIME_PER_HOUR"]), o, d)] += int(r["TOTAL_TRIPS"])
    for k in od:
        od[k] /= days[k[0]]     # -> trips per average day

    # ---------------------------------------------------------------- 1 + 3: route every trip
    routes = {}
    transfers = defaultdict(float); transfers_am = defaultdict(float); xfer_pair = defaultdict(float)
    seg = {"weekday": defaultdict(lambda: [0.0] * 24), "weekend": defaultdict(lambda: [0.0] * 24)}
    minutes = defaultdict(lambda: [0.0, 0.0])     # origin -> [trip-minutes, trips] weekday 6-9
    total_minutes = defaultdict(float)            # origin -> weekday daily train-minutes
    for (dt_, h, o, d), v in od.items():
        if o not in routes:
            routes[o] = routes_from(o)
        r = routes[o].get(d)
        if not r:
            continue
        cost, p = r
        for x, y in zip(p, p[1:]):
            if x[0] == y[0]:                       # change of line inside one station
                if dt_ == "weekday":
                    transfers[x[0]] += v; xfer_pair[(x[0], x[1], y[1])] += v
                    if h in (7, 8): transfers_am[x[0]] += v
            else:
                seg[dt_][f"{code_on[x]}>{code_on[y]}"][h] += v
        if dt_ == "weekday":
            total_minutes[o] += v * cost
            if h in (6, 7, 8):
                minutes[o][0] += v * cost; minutes[o][1] += v

    tap = defaultdict(lambda: {"weekday": [[0] * 24, [0] * 24], "weekend": [[0] * 24, [0] * 24]})
    pv_files = sorted(glob.glob("data/raw/lta/pv_train_*.csv"))
    pv_days = {"weekday": 0, "weekend": 0}
    for f in pv_files:
        c = day_counts(re.search(r"(\d{6})", f).group(1)); pv_days["weekday"] += c["weekday"]; pv_days["weekend"] += c["weekend"]
        for r in csv.DictReader(open(f, newline="", encoding="utf-8-sig")):
            sid = sid_of(r["PT_CODE"])
            if sid is None:
                continue
            h = int(r["TIME_PER_HOUR"]); rec = tap[sid][dtype(r["DAY_TYPE"])]
            rec[0][h] += int(r["TOTAL_TAP_IN_VOLUME"]); rec[1][h] += int(r["TOTAL_TAP_OUT_VOLUME"])
    for sid in tap:
        for dtp in ("weekday", "weekend"):
            tap[sid][dtp] = [[v / pv_days[dtp] for v in arr] for arr in tap[sid][dtp]]

    name = lambda sid: S[sid]["name"]
    lname = lambda l: st["lines"].get(l, {}).get("name", l)
    # transfers: compare with the station's own tap volume (hidden load)
    tr_rows = []
    for sid, v in sorted(transfers.items(), key=lambda kv: -kv[1])[:25]:
        taps = sum(tap[sid]["weekday"][0]) if sid in tap else 0
        top = sorted(((a, b, x) for (s2, a, b), x in xfer_pair.items() if s2 == sid), key=lambda t: -t[2])[:3]
        tr_rows.append({"name": name(sid), "transfers_per_day": round(v), "am_peak_per_day": round(transfers_am[sid]),
                        "tap_ins_per_day": round(taps), "hidden_ratio": round(v / taps, 2) if taps else None,
                        "top_moves": [{"from": lname(a), "to": lname(b), "per_day": round(x)} for a, b, x in top]})
    # busiest track segments (both directions shown separately), weekday 7-9am
    seg_rows = []
    for k, arr in seg["weekday"].items():
        a, b = k.split(">")
        seg_rows.append({"from": name(code2sid[a]), "to": name(code2sid[b]), "line": lname(re.match(r"[A-Z]+", a).group(0)),
                         "am_peak_per_day": round(arr[7] + arr[8]), "all_day": round(sum(arr))})
    seg_rows.sort(key=lambda r: -r["am_peak_per_day"])

    # ---------------------------------------------------------------- 3: commute minutes by station and town
    blocks = json.load(open("docs/data/hdb_blocks.json")) if os.path.exists("docs/data/hdb_blocks.json") else []
    town_of = {}
    for sid in open_now:
        w = defaultdict(float)
        for lat, lon, units, yr, town in blocks:
            if abs(lat - pos[sid][0]) < 0.012 and abs(lon - pos[sid][1]) < 0.012 and km((lat, lon), pos[sid]) < 1.0:
                w[town] += units
        if w:
            town_of[sid] = max(w, key=w.get)
    cm_rows = [{"name": name(o), "town": town_of.get(o, ""), "avg_minutes": round(m / n, 1), "am_trips_per_day": round(n)}
               for o, (m, n) in minutes.items() if n >= 1000]
    cm_rows.sort(key=lambda r: -r["avg_minutes"])
    town = defaultdict(lambda: [0.0, 0.0])
    for o, (m, n) in minutes.items():
        if o in town_of:
            town[town_of[o]][0] += m; town[town_of[o]][1] += n
    town_rows = sorted([{"town": t, "avg_minutes": round(m / n, 1), "am_trips_per_day": round(n)} for t, (m, n) in town.items() if n >= 2000],
                       key=lambda r: -r["avg_minutes"])

    # ---------------------------------------------------------------- 4 + 5: personalities, weekend ratio
    periods = [("5-7am", (5, 6)), ("7-10am", (7, 8, 9)), ("10am-4pm", tuple(range(10, 16))), ("4-7pm", (16, 17, 18)), ("7-10pm", (19, 20, 21)), ("after 10pm", (22, 23, 0))]
    feats, sids = [], []
    for sid, rec in tap.items():
        tin, tout = rec["weekday"]; ti, to = sum(tin), sum(tout)
        if ti < 2000 or S[sid]["mode"] != "MRT":
            continue
        we = sum(rec["weekend"][0]) + sum(rec["weekend"][1]); wd = ti + to
        f = [sum(tin[h] for h in hs) / ti for _, hs in periods] + [sum(tout[h] for h in hs) / to for _, hs in periods]
        f.append(0.5 * math.log(we / wd) if we and wd else 0)
        feats.append(f); sids.append(sid)
    fnames = [f"in {p}" for p, _ in periods] + [f"out {p}" for p, _ in periods] + ["weekend"]
    mean = [sum(c) / len(feats) for c in zip(*feats)]
    sd = [math.sqrt(sum((x[i] - mean[i]) ** 2 for x in feats) / len(feats)) or 1 for i in range(len(mean))]

    def metrics(members):
        def sh(arr, hs): return sum(arr[h] for h in hs) / (sum(arr) or 1)
        tin = [sum(tap[x]["weekday"][0][h] for x in members) for h in range(24)]
        tout = [sum(tap[x]["weekday"][1][h] for x in members) for h in range(24)]
        wd = sum(tin) + sum(tout); we = sum(sum(tap[x]["weekend"][0]) + sum(tap[x]["weekend"][1]) for x in members)
        return {"am_in": sh(tin, (7, 8, 9)), "am_out": sh(tout, (7, 8, 9)), "early_in": sh(tin, (5, 6)),
                "eve_in": sh(tin, (19, 20, 21, 22, 23)), "mid_out": sh(tout, range(10, 16)), "weekend_ratio": we / wd if wd else 0}, tin, tout
    allm, _, _ = metrics(sids)

    def label(m):
        if m["weekend_ratio"] >= 1.25: return "Weekend & event magnets"
        if m["am_out"] > 1.8 * m["am_in"] and m["weekend_ratio"] < 0.5: return "CBD & business parks"
        if m["am_out"] > m["am_in"]: return "Work, school & hospital destinations"
        if m["eve_in"] > 1.25 * allm["eve_in"]: return "Night-out & shopping hubs"
        if m["early_in"] > 1.2 * allm["early_in"]: return "Early-shift home towns"
        return "Home towns"
    # station-by-station rules (thresholds relative to the network) - transparent and easy to argue with
    sm = {x: metrics([x])[0] for x in sids}
    def pct(key, q, pool):
        v = sorted(sm[x][key] for x in pool); return v[min(len(v) - 1, int(q * len(v)))]
    homes = [x for x in sids if sm[x]["am_in"] >= sm[x]["am_out"]]
    dests = [x for x in sids if x not in homes]
    early_cut, eve_cut, ctr_cut = pct("early_in", .75, homes), pct("eve_in", .75, dests), pct("am_in", .25, homes)

    def label(x):
        m = sm[x]
        if m["weekend_ratio"] >= 1.2: return "Weekend & event magnets"
        if m["am_out"] >= 1.8 * m["am_in"] and m["weekend_ratio"] < 0.5: return "CBD & business parks"
        if x in dests: return "Shopping & night-out hubs" if m["eve_in"] >= eve_cut else "Work, school & hospital destinations"
        if m["early_in"] >= early_cut: return "Early-shift home towns"
        if m["am_in"] <= ctr_cut: return "Mixed town centres"
        return "Classic home towns"
    merged = defaultdict(list)
    for x in sids:
        merged[label(x)].append(x)
    pers = []
    for nm, members in sorted(merged.items(), key=lambda kv: -len(kv[1])):
        m, tin, tout = metrics(members)
        members = sorted(members, key=lambda x: -sum(tap[x]["weekday"][0]))
        ti, to = sum(tin), sum(tout)
        pers.append({"type": nm, "stations": [name(x) for x in members], "count": len(members),
                     "profile_in": [round(v / ti, 4) for v in tin], "profile_out": [round(v / to, 4) for v in tout],
                     "metrics": {k: round(v, 3) for k, v in m.items()}})

    wk_rows = []
    for sid, rec in tap.items():
        wd = sum(rec["weekday"][0]) + sum(rec["weekday"][1]); we = sum(rec["weekend"][0]) + sum(rec["weekend"][1])
        if wd >= 4000:
            wk_rows.append({"name": name(sid), "weekend_per_day": round(we), "weekday_per_day": round(wd), "ratio": round(we / wd, 2)})
    wk_rows.sort(key=lambda r: -r["ratio"])

    # ---------------------------------------------------------------- 6: morning vs evening
    am = defaultdict(float); pm = defaultdict(float)
    for (dt_, h, o, d), v in od.items():
        if dt_ != "weekday":
            continue
        if h in (6, 7, 8, 9): am[(o, d)] += v
        if 15 <= h <= 23 or h == 0: pm[(o, d)] += v
    st_am = defaultdict(float); st_back = defaultdict(float)
    for (o, d), v in am.items():
        st_am[o] += v; st_back[o] += min(v, pm.get((d, o), 0))
    mm_rows = sorted([{"name": name(o), "am_out_per_day": round(v), "come_back_by_mrt_pct": round(100 * st_back[o] / v, 1)}
                      for o, v in st_am.items() if v >= 2000], key=lambda r: r["come_back_by_mrt_pct"])
    pair_rows = sorted([{"from": name(o), "to": name(d), "am_per_day": round(v), "pm_back_per_day": round(pm.get((d, o), 0)),
                         "back_pct": round(100 * pm.get((d, o), 0) / v, 1)} for (o, d), v in am.items() if v >= 300],
                       key=lambda r: r["back_pct"])[:15]

    # ---------------------------------------------------------------- 2: bus -> MRT feeder trips
    bus = {"available": False}
    stops = json.load(open("data/raw/lta/bus_stops.json")) if os.path.exists("data/raw/lta/bus_stops.json") else {}
    bus_files = sorted(glob.glob("data/archive/lta/bus_od/*.zip"))
    if stops and bus_files:
        mrt_pts = [(sid, pos[sid]) for sid in open_now if S[sid]["mode"] == "MRT"]
        near = {}
        for code, (lon, lat, *_rest) in stops.items():
            best = min(((km((lat, lon), p), sid) for sid, p in mrt_pts), default=(99, None))
            near[code] = best
        feeder_by_stop = defaultdict(float); all_by_stop = defaultdict(float); feeder_into = defaultdict(float)
        bdays = 0
        for f in bus_files:
            bdays += day_counts(os.path.basename(f)[:6])["weekday"]
            z = zipfile.ZipFile(f); nm = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
            for r in csv.reader(io.TextIOWrapper(z.open(nm), encoding="utf-8-sig")):
                if r[1] != "WEEKDAY" or r[2] not in ("6", "7", "8"):
                    continue
                o, d, v = r[4], r[5], int(r[6])
                if o not in near or d not in near:
                    continue
                all_by_stop[o] += v
                if near[d][0] <= 0.3 and near[o][0] > 0.5:   # ends at a station, starts away from one
                    feeder_by_stop[o] += v; feeder_into[near[d][1]] += v
        # 400 m grid of where feeder trips start
        cells = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, defaultdict(float)])
        for code, v in feeder_by_stop.items():
            lon, lat = stops[code][0], stops[code][1]
            k = (round(lat / 0.0036), round(lon / 0.0036))
            c = cells[k]; c[0] += v; c[1] += lat * v; c[2] += lon * v; c[3] += all_by_stop[code]
        pts = []
        for k, (v, la, lo, allv, _) in cells.items():
            v /= bdays
            if v < 150:
                continue
            lat, lon = la / (v * bdays), lo / (v * bdays)
            nearest_town = ""
            if blocks:
                b = min(blocks, key=lambda b: (b[0] - lat) ** 2 + (b[1] - lon) ** 2)
                nearest_town = b[4] if km((b[0], b[1]), (lat, lon)) < 1.0 else ""
            dist_mrt = min(km((lat, lon), p) for _, p in mrt_pts)
            pts.append({"lat": round(lat, 5), "lon": round(lon, 5), "feeder_trips_per_day": round(v), "town": nearest_town,
                        "km_to_mrt": round(dist_mrt, 2), "feeder_share_pct": round(100 * v * bdays / allv, 1) if allv else None})
        pts.sort(key=lambda p: -p["feeder_trips_per_day"])
        bus = {"available": True, "months": [os.path.basename(f)[:6] for f in bus_files],
               "areas": pts[:300],
               "stations_fed": sorted([{"name": name(s), "feeder_arrivals_per_day": round(v / bdays)} for s, v in feeder_into.items()],
                                      key=lambda r: -r["feeder_arrivals_per_day"])[:20],
               "total_feeder_trips_am": round(sum(feeder_by_stop.values()) / bdays)}

    # ---------------------------------------------------------------- 7: heartbeat flows
    hb = {"hours": {}}
    for h in list(range(5, 24)) + [0]:
        flows = sorted(((o, d, v) for (dt_, hh, o, d), v in od.items() if dt_ == "weekday" and hh == h), key=lambda t: -t[2])
        tot = sum(v for *_, v in flows)
        hb["hours"][h] = {"total": round(tot), "flows": [[o, d, round(v)] for o, d, v in flows[:400]]}
    hb["stations"] = {sid: [S[sid]["name"], pos[sid][0], pos[sid][1]] for sid in open_now}
    hb["months"] = months; hb["generated"] = dt.datetime.now(SGT).isoformat(timespec="minutes")
    json.dump(hb, open("docs/data/heartbeat.json", "w"), separators=(",", ":"))

    json.dump({k: {kk: [round(x, 1) for x in vv] for kk, vv in v.items()} for k, v in seg.items()} | {"months": months, "note": "trips per average day by departure hour, routed on today's network"},
              open("docs/data/segment_loads.json", "w"), separators=(",", ":"))
    out = {"generated": dt.datetime.now(SGT).isoformat(timespec="minutes"), "months": months, "days": days,
           "assumptions": {"speed_kmh": SPEED, "dwell_min": DWELL, "transfer_min": TRANSFER_MIN},
           "transfers": tr_rows, "busiest_segments_am": seg_rows[:20],
           "commute_stations": cm_rows[:25], "commute_stations_shortest": cm_rows[-10:][::-1], "commute_towns": town_rows,
           "personalities": pers, "weekend": {"magnets": wk_rows[:20], "commuter_only": wk_rows[-15:][::-1]},
           "mismatch": {"stations": mm_rows[:20], "best_return": mm_rows[-10:][::-1], "pairs": pair_rows},
           "bus": bus}
    # ---------------------------------------------------------------- 4b: do regions keep their own jobs?
    REGION = {"Woodlands": "North", "Sembawang": "North", "Yishun": "North",
              "Ang Mo Kio": "North-East", "Hougang": "North-East", "Punggol": "North-East", "Sengkang": "North-East", "Serangoon": "North-East",
              "Bedok": "East", "Pasir Ris": "East", "Tampines": "East",
              "Bukit Batok": "West", "Bukit Panjang": "West", "Choa Chu Kang": "West", "Clementi": "West", "Jurong East": "West", "Jurong West": "West", "Tengah": "West"}
    RP = (1.28393, 103.85143)
    region_of = {}
    with_town = [x for x in open_now if x in town_of]
    for sid in open_now:
        if km(pos[sid], RP) < 3.0:
            region_of[sid] = "City core"; continue
        t = town_of.get(sid) or town_of[min(with_town, key=lambda y: km(pos[sid], pos[y]))]
        region_of[sid] = REGION.get(t, "Central")
    CENTRES = {"Jurong Lake District": ["Jurong East", "Chinese Garden", "Lakeside"], "Woodlands Regional Centre": ["Woodlands", "Woodlands South", "Woodlands North"],
               "Tampines Regional Centre": ["Tampines", "Tampines East", "Tampines West"], "Punggol Digital District": ["Punggol Coast", "Punggol"],
               "one-north / Buona Vista": ["one-north", "Buona Vista", "Kent Ridge"], "Paya Lebar": ["Paya Lebar"], "City core (Raffles Place area)": None}
    reg = defaultdict(lambda: defaultdict(float))
    for (dt_, h, o, d), v in od.items():
        if dt_ == "weekday" and h in (6, 7, 8) and o in region_of and d in region_of:
            reg[region_of[o]][region_of[d]] += v
    regions_out = []
    for rname in ["North", "North-East", "East", "West", "Central"]:
        row = reg.get(rname, {}); tot = sum(row.values()) or 1
        regions_out.append({"region": rname, "am_trips_per_day": round(tot), "stay_in_region_pct": round(100 * row.get(rname, 0) / tot, 1),
                            "to_city_core_pct": round(100 * row.get("City core", 0) / tot, 1),
                            "to_central_pct": round(100 * row.get("Central", 0) / tot, 1),
                            "to": {k: round(100 * v / tot, 1) for k, v in sorted(row.items(), key=lambda kv: -kv[1])}})
    nm2sid = {S[x]["name"]: x for x in open_now}
    centres_out = []
    for cname, members in CENTRES.items():
        ids = {x for x, r in region_of.items() if r == "City core"} if members is None else {nm2sid[m] for m in members if m in nm2sid}
        arr = defaultdict(float)
        for (dt_, h, o, d), v in od.items():
            if dt_ == "weekday" and h in (6, 7, 8, 9) and d in ids and o not in ids:
                arr[region_of.get(o, "?")] += v
        tot = sum(arr.values()) or 1
        home = "City core" if members is None else region_of.get(next(iter(ids)), "?") if ids else "?"
        centres_out.append({"centre": cname, "am_arrivals_per_day": round(sum(arr.values())), "from_own_region_pct": round(100 * arr.get(home, 0) / tot, 1) if members else None,
                            "region": home, "from": {k: round(100 * v / tot, 1) for k, v in sorted(arr.items(), key=lambda kv: -kv[1])}})

    # ---------------------------------------------------------------- per-station + per-segment values for the map
    stype = {x: t["type"] for t in pers for x in []}
    for t in pers:
        for nm_ in t["stations"]:
            stype[nm_] = t["type"]
    back = {o: 100 * st_back[o] / v for o, v in st_am.items() if v >= 500}
    seg_am = defaultdict(float)
    for k, arr in seg["weekday"].items():
        a, b = k.split(">"); ia, ib = code2sid[a], code2sid[b]
        key = (min(ia, ib), max(ia, ib), re.match(r"[A-Z]+", a).group(0))
        seg_am[key] = max(seg_am[key], arr[7] + arr[8])        # busier direction, 7-9am
    near_load = defaultdict(float)
    for (ia, ib, _), v in seg_am.items():
        near_load[ia] = max(near_load[ia], v); near_load[ib] = max(near_load[ib], v)
    per = {}
    for sid in open_now:
        taps = sum(tap[sid]["weekday"][0]) if sid in tap else 0
        wd = taps + (sum(tap[sid]["weekday"][1]) if sid in tap else 0)
        we = (sum(tap[sid]["weekend"][0]) + sum(tap[sid]["weekend"][1])) if sid in tap else 0
        m, n = minutes.get(sid, (0, 0))
        per[sid] = {"taps_in": round(taps), "transfers": round(transfers.get(sid, 0)), "transfers_am": round(transfers_am.get(sid, 0)),
                    "minutes": round(m / n, 1) if n >= 300 else None, "type": stype.get(name(sid)),
                    "weekend_ratio": round(we / wd, 2) if wd >= 2000 else None, "back_pct": round(back[sid], 1) if sid in back else None,
                    "am_track_load": round(near_load.get(sid, 0))}
    out["per_station"] = per
    out["regions"] = regions_out; out["centres"] = centres_out
    out["segments_am"] = [[ia, ib, l, round(v)] for (ia, ib, l), v in sorted(seg_am.items(), key=lambda kv: -kv[1])]
    json.dump(out, open("docs/data/insights.json", "w"), separators=(",", ":"))
    print(f"insights.json: {len(tr_rows)} interchanges, {len(pers)} station types, bus={'yes' if bus['available'] else 'no'}; months {months}")


if __name__ == "__main__":
    main()
