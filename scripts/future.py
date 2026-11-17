"""2030 crowding stress test -> docs/data/future.json (rendered by docs/future.html)

Today's weekday morning trips (LTA origin-destination data) are routed twice:
  * on today's network, and
  * on the network LTA says will be open by mid-2030 (TEL stage 5, JRL stages 1-3, CRL phase 1, ...) with the residents of
    every new BTO / land-sale site in data/manual/future_housing.csv added as extra trips.
The difference shows which stretches of track get busier, which get relief, and how full the new lines will be.

New residents' trips: residents x RAIL_SHARE return trips a weekday, starting at the nearest station on the 2030 network,
spread over destinations like the trips that start at that station today (or the network average for brand-new stations).
"""
import csv, glob, heapq, io, json, math, os, re, sys, zipfile, datetime as dt
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sgdays import day_counts

SGT = dt.timezone(dt.timedelta(hours=8))
YEAR_2030 = 2030.6
PERSONS_PER_UNIT, RAIL_SHARE = 3.1, 0.35
AM = (7, 8)
SPEED = {"MRT": 40.0, "LRT": 25.0}; DWELL, TRANSFER_MIN = 0.5, 5.0
CAP = {"NS": 1920, "EW": 1920, "CG": 1920, "NE": 1920, "CC": 930, "DT": 930, "TE": 1280, "JS": 600, "JW": 600, "JE": 600, "CR": 1920, "CP": 1920}
TPH = {"NS": 27, "EW": 27, "CG": 12, "NE": 24, "CC": 24, "DT": 24, "TE": 20, "JS": 20, "JW": 20, "JE": 20, "CR": 24, "CP": 12}


def km(a, b):
    return math.hypot((a[1] - b[1]) * 111.32 * math.cos(math.radians(1.35)), (a[0] - b[0]) * 110.57)


def build(st, year):
    pos = {s["id"]: (s["lat"], s["lon"]) for s in st["stations"]}
    adj, code_on = defaultdict(list), {}
    for e in st["edges"]:
        if e["open_year"] > year or (e["until"] is not None and year >= e["until"]):
            continue
        c1, c2 = e["codes"]; line = e["line"]
        if line in ("STC", "PTC"):
            line = re.match(r"[A-Z]+", c2).group(0)
        mode = st["lines"].get(line, {}).get("mode", "MRT")
        a, b = (e["a"], line), (e["b"], line); code_on[a] = c1; code_on[b] = c2
        t = km(pos[e["a"]], pos[e["b"]]) / SPEED[mode] * 60 + DWELL
        adj[a].append((b, t)); adj[b].append((a, t))
    by_station = defaultdict(list)
    for n in list(adj):
        by_station[n[0]].append(n)
    for nodes in by_station.values():
        for x in nodes:
            for y in nodes:
                if x != y:
                    adj[x].append((y, TRANSFER_MIN))
    return adj, by_station, code_on


def route_all(adj, by_station, origins):
    paths = {}
    for o in origins:
        dist, prev, pq = {}, {}, []
        for n in by_station.get(o, []):
            dist[n] = 0.0; heapq.heappush(pq, (0.0, n))
        while pq:
            d, n = heapq.heappop(pq)
            if d > dist.get(n, 1e18):
                continue
            for m, w in adj[n]:
                if d + w < dist.get(m, 1e18) - 1e-9:
                    dist[m] = d + w; prev[m] = n; heapq.heappush(pq, (d + w, m))
        for dsid, nodes in by_station.items():
            best = min(nodes, key=lambda n: dist.get(n, 1e18))
            if dist.get(best, 1e18) >= 1e18:
                continue
            p = [best]
            while p[-1] in prev:
                p.append(prev[p[-1]])
            p.reverse()
            while len(p) > 1 and p[0][0] == p[1][0]:
                p.pop(0)
            paths[(o, dsid)] = (dist[best], p)
    return paths


def assign(trips, paths, code_on):
    seg, xfer, mins, n = defaultdict(float), defaultdict(float), 0.0, 0.0
    for (o, d), v in trips.items():
        r = paths.get((o, d))
        if not r:
            continue
        cost, p = r; mins += cost * v; n += v
        for x, y in zip(p, p[1:]):
            if x[0] == y[0]:
                xfer[x[0]] += v
            else:
                seg[f"{code_on[x]}>{code_on[y]}"] += v
    return seg, xfer, (mins / n if n else 0)


def main():
    now_year = dt.date.today().year + dt.date.today().timetuple().tm_yday / 366
    st = json.load(open("docs/data/stations.json"))
    S = {s["id"]: s for s in st["stations"]}
    code2sid = {c["code"]: s["id"] for s in st["stations"] for c in s["codes"]}
    open_now = {sid for sid, s in S.items() if s["open_year"] <= now_year}
    open_30 = {sid for sid, s in S.items() if s["open_year"] <= YEAR_2030}

    def sid_of(key):
        for c in re.split(r"[-/ ]+", key.strip()):
            if c in code2sid and code2sid[c] in open_now:
                return code2sid[c]

    # today's weekday 7-9am trips, per average weekday
    od_files = sorted(glob.glob("data/archive/lta/train_od/*.zip"))
    wd = sum(day_counts(os.path.basename(f)[:6])["weekday"] for f in od_files)
    trips = defaultdict(float); all_day = 0.0
    for f in od_files:
        z = zipfile.ZipFile(f); nm = [n for n in z.namelist() if n.lower().endswith(".csv")][0]; cache = {}
        for r in csv.DictReader(io.TextIOWrapper(z.open(nm), encoding="utf-8-sig")):
            if r["DAY_TYPE"] != "WEEKDAY":
                continue
            all_day += int(r["TOTAL_TRIPS"]) / wd
            if int(r["TIME_PER_HOUR"]) not in AM:
                continue
            o = cache[r["ORIGIN_PT_CODE"]] if r["ORIGIN_PT_CODE"] in cache else cache.setdefault(r["ORIGIN_PT_CODE"], sid_of(r["ORIGIN_PT_CODE"]))
            d = cache[r["DESTINATION_PT_CODE"]] if r["DESTINATION_PT_CODE"] in cache else cache.setdefault(r["DESTINATION_PT_CODE"], sid_of(r["DESTINATION_PT_CODE"]))
            if o is not None and d is not None and o != d:
                trips[(o, d)] += int(r["TOTAL_TRIPS"]) / wd

    # new residents -> extra morning trips (only the share that falls in 7-9am, like today's trips)
    total_am = sum(trips.values())
    am_share = total_am / all_day      # share of a weekday's trips that start 7-9am (from the LTA data)
    dest_from = defaultdict(lambda: defaultdict(float)); dest_all = defaultdict(float)
    for (o, d), v in trips.items():
        dest_from[o][d] += v; dest_all[d] += v
    added, new_sites = defaultdict(float), []
    stn30 = [S[x] for x in open_30 if S[x]["mode"] == "MRT"]
    for f in csv.DictReader(open("data/manual/future_housing.csv")):
        p = (float(f["lat"]), float(f["lon"]))
        s = min(stn30, key=lambda s: km(p, (s["lat"], s["lon"])))
        am_trips = int(f["units"]) * PERSONS_PER_UNIT * RAIL_SHARE * 2 * am_share * 0.5   # half of the return trips are outbound in the morning
        pattern = dest_from.get(s["id"]) or dest_all
        tot = sum(pattern.values())
        for d, v in pattern.items():
            if d != s["id"]:
                added[(s["id"], d)] += am_trips * v / tot
        new_sites.append({"name": f["name"], "units": int(f["units"]), "station": s["name"], "km": round(km(p, (s["lat"], s["lon"])), 2), "am_trips": round(am_trips)})

    adj0, bs0, co0 = build(st, now_year); adj1, bs1, co1 = build(st, YEAR_2030)
    p0 = route_all(adj0, bs0, {o for o, _ in trips}); p1 = route_all(adj1, bs1, {o for o, _ in list(trips) + list(added)})
    seg0, x0, m0 = assign(trips, p0, co0)
    trips30 = defaultdict(float, trips)
    for k, v in added.items():
        trips30[k] += v
    seg1, x1, m1 = assign(trips30, p1, co1)

    def load_pct(key, v):
        line = re.match(r"[A-Z]+", key).group(0)
        return 100 * v / 2 / (TPH.get(line, 20) * CAP.get(line, 1000))        # 2-hour total -> per hour vs capacity
    name = lambda code: S[code2sid[code]]["name"]
    lname = lambda code: st["lines"].get(re.match(r"[A-Z]+", code).group(0), {}).get("name", code)
    rows = []
    for k in set(seg0) | set(seg1):
        a, b = k.split(">"); v0, v1 = seg0.get(k, 0), seg1.get(k, 0)
        rows.append({"stretch": f"{name(a)} → {name(b)}", "line": lname(a), "now": round(v0 / 2), "y2030": round(v1 / 2),
                     "change": round((v1 - v0) / 2), "change_pct": round(100 * (v1 - v0) / v0, 1) if v0 >= 200 else None,
                     "load_now_pct": round(load_pct(k, v0)), "load_2030_pct": round(load_pct(k, v1)), "new": v0 == 0})
    worse = sorted([r for r in rows if not r["new"]], key=lambda r: -r["change"])[:15]
    relief = sorted([r for r in rows if not r["new"] and r["y2030"] > 0], key=lambda r: r["change"])[:15]   # y2030 == 0: stretch split by an infill station
    open_prefixes = {re.match(r"[A-Z]+", c["code"]).group(0) for s_ in st["stations"] for c in s_["codes"] if c["open_year"] == 0}
    def is_new_line(stretch_key):
        a, b = stretch_key.split(">")
        return re.match(r"[A-Z]+", a).group(0) not in open_prefixes or {a, b} & {"TE30", "TE31", "DT36", "DT37"}
    newl = sorted([r | {"key": k} for k in seg1 for r in [next(x for x in rows if x["stretch"] == f"{name(k.split('>')[0])} → {name(k.split('>')[1])}" and x["line"] == lname(k.split('>')[0]))]
                   if k not in seg0 and is_new_line(k)], key=lambda r: -r["y2030"])[:20]
    fullest30 = sorted(rows, key=lambda r: -r["load_2030_pct"])[:12]
    xf = sorted([{"name": S[s]["name"], "now": round(x0.get(s, 0) / 2), "y2030": round(v / 2), "change": round((v - x0.get(s, 0)) / 2)} for s, v in x1.items()],
                key=lambda r: -abs(r["change"]))[:12]
    new_lines = sorted({st["lines"][e["line"]]["name"] for e in st["edges"] if now_year < e["open_year"] <= YEAR_2030 and e["line"] in st["lines"]})
    out = {"generated": dt.datetime.now(SGT).isoformat(timespec="minutes"), "year": YEAR_2030, "months": [os.path.basename(f)[:6] for f in od_files],
           "lines_added": new_lines, "new_stations": sorted(S[x]["name"] for x in open_30 - open_now),
           "trips_per_hour_now": round(sum(trips.values()) / 2), "added_trips_per_hour": round(sum(added.values()) / 2),
           "avg_minutes_now": round(m0, 1), "avg_minutes_2030": round(m1, 1),
           "busier": worse, "relief": relief, "new_line_loads": newl, "fullest_2030": fullest30, "interchanges": xf, "new_homes": new_sites,
           "assumptions": {"persons_per_unit": PERSONS_PER_UNIT, "rail_share": RAIL_SHARE, "am_share": round(am_share, 2), "capacity": CAP, "trains_per_hour": TPH,
                           "note": "Existing trips keep their start and end stations, so people who would switch to a closer new station (e.g. on the Cross Island Line) and new jobs are not modelled: loads on brand-new lines are a floor, not a forecast."}}
    json.dump(out, open("docs/data/future.json", "w"), separators=(",", ":"))
    print(f"future.json: {len(new_lines)} new lines, {len(out['new_stations'])} new stations; avg {m0:.1f} -> {m1:.1f} min")


if __name__ == "__main__":
    main()
