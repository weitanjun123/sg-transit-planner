"""Find where people live too far from an MRT station, and propose new stations and lines.

Inputs (all in docs/data unless noted)
  stations.json            every open + announced MRT/LRT station (scripts/build_stations.py)
  hdb_blocks.json          every HDB block with dwelling units (scripts/fetch_hdb.py)
  data/manual/future_housing.csv   BTO launches + Government Land Sales sites (curated, sourced)
  station_hourly.json      LTA monthly tap-ins per station per hour (optional, scripts/fetch_lta.py)
  crowd_forecast.json      LTA platform crowd forecast (optional)
Output
  analysis.json            gaps, sites already fixed by LTA plans, proposed stations and lines

Method (all parameters in PARAMS, printed into the output so anyone can reproduce/tune):
 1. residents = dwelling units x average household size, on a 300 m grid
 2. a cell is "served" if an MRT station is within ~800 m straight line of its centre (about a 10-minute walk
    to the nearest station entrance; entrances sit up to ~200 m from the station centre point)
 3. unserved residents are weighted up when their current nearest station is crowded in the AM peak (its own tap-ins,
    the load on the busiest track next to it, or line changes there - from routed LTA trips), and again
    when many people there take a morning bus just to reach an MRT station (LTA bus origin-destination data)
 4. greedy siting: repeatedly place a new station where it would serve the most weighted unserved residents,
    at least 1 km from any existing/planned station, until a station would serve too few people
 5. new stations are chained into lines (max 3 km apart, no sharp turns) and both ends are tied into the
    nearest MRT station as interchanges; single sites next to an existing line become infill stations
"""
import csv, json, math, os, datetime as dt
from collections import defaultdict

PARAMS = {
    "grid_m": 300, "persons_per_unit": 3.1, "walk_radius_m": 800, "lrt_counts_as": 0.5,
    "min_site_spacing_m": 1000, "min_new_residents": 6000, "max_link_m": 3000, "max_turn_deg": 70,
    "max_tie_in_m": 3500, "infill_max_offset_m": 700, "crowd_weight": 1.0,
    "bus_feeder_radius_m": 600, "bus_feeder_weight": 1.0,
}
D = "docs/data"
LAT0, LON0 = 1.3521, 103.8198
KX, KY = 111320 * math.cos(math.radians(LAT0)), 110574


def xy(lat, lon):
    return (lon - LON0) * KX, (lat - LAT0) * KY


def ll(x, y):
    return round(y / KY + LAT0, 6), round(x / KX + LON0, 6)


def load(name, default=None):
    p = f"{D}/{name}"
    return json.load(open(p)) if os.path.exists(p) else default


class Grid:
    """Spatial hash for fast radius queries."""
    def __init__(self, pts, cell=1000):
        self.cell, self.b = cell, defaultdict(list)
        for i, (x, y) in enumerate(pts):
            self.b[(int(x // cell), int(y // cell))].append(i)
        self.pts = pts

    def near(self, x, y, r):
        c, out = self.cell, []
        for gx in range(int((x - r) // c), int((x + r) // c) + 1):
            for gy in range(int((y - r) // c), int((y + r) // c) + 1):
                for i in self.b.get((gx, gy), []):
                    px, py = self.pts[i]
                    if (px - x) ** 2 + (py - y) ** 2 <= r * r:
                        out.append(i)
        return out


def nearest(x, y, pts):
    best, bi = 1e18, -1
    for i, (px, py) in enumerate(pts):
        d = (px - x) ** 2 + (py - y) ** 2
        if d < best:
            best, bi = d, i
    return bi, math.sqrt(best)


def seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy), t


def crowd_index(stations, now_year):
    """0..1 per open MRT station: AM-peak (07-09h weekday) tap-ins vs the busiest station, and/or LTA crowd forecast."""
    idx = defaultdict(float)
    code_to_sid = {c["code"]: s["id"] for s in stations for c in s["codes"]}
    hourly = load("station_hourly.json")
    if hourly:
        vol = defaultdict(int)
        for key, rec in hourly["weekday"].items():
            sids = {code_to_sid[c] for c in key.split("/") if c in code_to_sid}
            for sid in sids:
                vol[sid] += sum(rec["in"][7:9])
        top = max(vol.values() or [1])
        for sid, v in vol.items():
            idx[sid] = max(idx[sid], v / top)
    fc = load("crowd_forecast.json")
    if fc:
        for code, slots in fc.get("stations", {}).items():
            am = [lvl for t, lvl in slots if "07:00" <= t < "09:00"]
            if am and code in code_to_sid:
                share = sum(1 for l in am if l == "h") / len(am) + 0.5 * sum(1 for l in am if l == "m") / len(am)
                idx[code_to_sid[code]] = max(idx[code_to_sid[code]], share)
    # routed trips (scripts/insights.py): how full the busiest track next to each station is at 7-9am, and line changes there
    ins = load("insights.json")
    if ins and ins.get("per_station"):
        ps = ins["per_station"]
        top_load = max((v["am_track_load"] for v in ps.values()), default=0) or 1
        top_x = max((v["transfers_am"] for v in ps.values()), default=0) or 1
        for sid, v in ps.items():
            sid = int(sid)
            idx[sid] = max(idx[sid], v["am_track_load"] / top_load, v["transfers_am"] / top_x)
    return idx, bool(hourly), bool(fc)


def main():
    P = PARAMS
    now_year = dt.date.today().year + (dt.date.today().timetuple().tm_yday / 366)
    st = load("stations.json")
    stations = st["stations"]
    mrt_all = [s for s in stations if s["mode"] == "MRT"]
    mrt_now = [s for s in mrt_all if s["open_year"] <= now_year]
    lrt_now = [s for s in stations if s["mode"] == "LRT"]
    P_all = [xy(s["lat"], s["lon"]) for s in mrt_all]
    P_now = [xy(s["lat"], s["lon"]) for s in mrt_now]
    P_lrt = [xy(s["lat"], s["lon"]) for s in lrt_now]
    G_all, G_now, G_lrt = Grid(P_all), Grid(P_now), Grid(P_lrt)

    # ---- 1. residents on a grid -------------------------------------------------------------
    cells = defaultdict(lambda: {"now": 0.0, "future": 0.0, "towns": defaultdict(float), "names": set()})
    g = P["grid_m"]
    blocks = load("hdb_blocks.json", [])
    for lat, lon, units, year, town in blocks:
        x, y = xy(lat, lon)
        c = cells[(int(x // g), int(y // g))]
        c["now"] += units * P["persons_per_unit"]; c["towns"][town.title() if town else ""] += units
    future = list(csv.DictReader(open("data/manual/future_housing.csv")))
    for f in future:
        x, y = xy(float(f["lat"]), float(f["lon"]))
        c = cells[(int(x // g), int(y // g))]
        c["future"] += int(f["units"]) * P["persons_per_unit"]; c["names"].add(f["name"])

    crowd, has_pv, has_fc = crowd_index(mrt_now, now_year)
    # morning bus trips that start here and end at an MRT station (people bussing to the train): from scripts/insights.py
    ins = load("insights.json", {}) or {}
    feeders = [(xy(a["lat"], a["lon"]), a["feeder_trips_per_day"]) for a in ins.get("bus", {}).get("areas", [])]
    G_f = Grid([p for p, _ in feeders]) if feeders else None

    # ---- 2+3. coverage + weighting ---------------------------------------------------------
    keys = list(cells)
    centers = [((k[0] + .5) * g, (k[1] + .5) * g) for k in keys]
    gaps, fixed_by_plan = [], defaultdict(float)
    weight = {}
    tot_now = tot_future = un_now = un_after_plans = 0.0
    R = P["walk_radius_m"]
    for k, (x, y) in zip(keys, centers):
        c = cells[k]
        pop = c["now"] + c["future"]
        tot_now += c["now"]; tot_future += c["future"]
        served_now = bool(G_now.near(x, y, R))
        served_all = bool(G_all.near(x, y, R))
        if not served_now:
            un_now += c["now"]
        if served_all and not served_now:
            j, _ = nearest(x, y, P_all)
            fixed_by_plan[mrt_all[j]["name"]] += pop
        if served_all:
            continue
        un_after_plans += pop
        lrt = P["lrt_counts_as"] if G_lrt.near(x, y, R) else 1.0
        jn, dn = nearest(x, y, P_now)
        fb = sum(feeders[i][1] for i in G_f.near(x, y, P["bus_feeder_radius_m"])) if G_f else 0
        w = pop * lrt * (1 + P["crowd_weight"] * crowd.get(mrt_now[jn]["id"], 0)) * (1 + P["bus_feeder_weight"] * min(1.0, fb / 2000))
        weight[k] = (w, pop, mrt_now[jn]["id"])
        lat, lon = ll(x, y)
        gaps.append([lat, lon, round(pop), round(w), round(dn), mrt_now[jn]["id"]])

    # ---- 4. greedy station siting ----------------------------------------------------------
    cand = [k for k in keys if k in weight]
    kidx = {k: i for i, k in enumerate(keys)}
    cpts = [centers[kidx[k]] for k in cand]
    G_c = Grid(cpts) if cpts else None
    covered, sites = set(), []
    taken = list(P_all)
    G_taken = Grid(taken, 2000)
    while G_c:
        best = None
        for i, (x, y) in enumerate(cpts):
            if any(math.hypot(x - tx, y - ty) < P["min_site_spacing_m"] for tx, ty in [taken[j] for j in G_taken.near(x, y, P["min_site_spacing_m"])]):
                continue
            ids = [j for j in G_c.near(x, y, R) if cand[j] not in covered]
            w = sum(weight[cand[j]][0] for j in ids)
            if best is None or w > best[0]:
                best = (w, i, ids)
        if not best:
            break
        w, i, ids = best
        pop = sum(weight[cand[j]][1] for j in ids)
        if pop < P["min_new_residents"]:
            break
        x, y = cpts[i]
        towns, names, relieved = defaultdict(float), set(), defaultdict(float)
        for j in ids:
            c = cells[cand[j]]
            for t, u in c["towns"].items(): towns[t] += u
            names |= c["names"]
            relieved[weight[cand[j]][2]] += weight[cand[j]][1]
            covered.add(cand[j])
        town = max(towns, key=towns.get) if towns and max(towns.values()) > 0 else ""
        jn, dn = nearest(x, y, P_now)
        ns = mrt_now[jn]; sx, sy = xy(ns["lat"], ns["lon"])
        comp = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"][int(((math.degrees(math.atan2(y - sy, x - sx)) + 22.5) % 360) // 45)]
        label = f"{town or (sorted(names)[0] if names else 'site')} ({dn/1000:.1f} km {comp} of {ns['name']})"
        lat, lon = ll(x, y)
        sid = {s["id"]: s for s in stations}
        sites.append({"id": len(sites) + 1, "lat": lat, "lon": lon, "area": label, "new_residents": round(pop),
                      "weighted": round(w), "future_projects": sorted(names),
                      "relieves": [{"station": sid[k]["name"], "residents": round(v), "am_crowd": round(crowd.get(k, 0), 2)}
                                   for k, v in sorted(relieved.items(), key=lambda kv: -kv[1])[:3]]})
        taken.append((x, y)); G_taken = Grid(taken, 2000)

    # ---- 5. chain sites into lines ---------------------------------------------------------
    S = {s["id"]: xy(s["lat"], s["lon"]) for s in sites}
    free = set(S)
    chains = []
    for s in sorted(sites, key=lambda s: -s["weighted"]):
        if s["id"] not in free:
            continue
        chain = [s["id"]]; free.discard(s["id"])
        for end in (1, 0):
            while True:
                tip = chain[-1] if end else chain[0]
                prev = (chain[-2] if end else chain[1]) if len(chain) > 1 else None
                tx, ty = S[tip]
                best = None
                for o in free:
                    ox, oy = S[o]
                    d = math.hypot(ox - tx, oy - ty)
                    if d > P["max_link_m"]:
                        continue
                    if prev is not None:
                        px, py = S[prev]
                        a1 = math.atan2(ty - py, tx - px); a2 = math.atan2(oy - ty, ox - tx)
                        if abs((math.degrees(a2 - a1) + 180) % 360 - 180) > P["max_turn_deg"]:
                            continue
                    if best is None or d < best[0]:
                        best = (d, o)
                if not best:
                    break
                free.discard(best[1])
                if end: chain.append(best[1])
                else: chain.insert(0, best[1])
        chains.append(chain)

    edges_now = [e for e in st["edges"] if e["open_year"] <= 9999 and e["until"] is None
                 and st["lines"].get(e["line"], {}).get("mode") == "MRT"]
    by_id = {s["id"]: s for s in stations}
    lines, infill = [], []
    letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for chain in chains:
        pts = [S[c] for c in chain]
        if len(chain) == 1:
            best = None
            for e in edges_now:
                a, b = by_id[e["a"]], by_id[e["b"]]
                d, t = seg_dist(pts[0], xy(a["lat"], a["lon"]), xy(b["lat"], b["lon"]))
                if 0.15 < t < 0.85 and (best is None or d < best[0]):
                    best = (d, e, a, b)
            site = next(s for s in sites if s["id"] == chain[0])
            if best and best[0] <= P["infill_max_offset_m"]:
                site["type"] = "infill"
                site["line"] = st["lines"][best[1]["line"]]["name"]
                site["between"] = [best[2]["name"], best[3]["name"]]
                infill.append(site); continue
        # tie both ends into the network
        ends = []
        for tip, nxt in ((pts[0], pts[1] if len(pts) > 1 else None), (pts[-1], pts[-2] if len(pts) > 1 else None)):
            best = None
            for j, (sx, sy) in enumerate(P_all):
                d = math.hypot(sx - tip[0], sy - tip[1])
                if d > P["max_tie_in_m"]:
                    continue
                if nxt is not None:  # do not double back over the line
                    a1 = math.atan2(tip[1] - nxt[1], tip[0] - nxt[0]); a2 = math.atan2(sy - tip[1], sx - tip[0])
                    if abs((math.degrees(a2 - a1) + 180) % 360 - 180) > 100:
                        continue
                if best is None or d < best[0]:
                    best = (d, j)
            ends.append(mrt_all[best[1]] if best else None)
        if len(chain) == 1 and ends[0] and ends[1] and ends[0]["id"] == ends[1]["id"]:
            ends[1] = None
        if len(chain) == 1 and not (ends[0] and ends[1]):
            site = next(s for s in sites if s["id"] == chain[0])
            site["type"] = "single"
            site["line"] = "new station or short spur"
            site["between"] = [e["name"] for e in ends if e] or ["-"]
            infill.append(site); continue
        if not any(ends):
            continue
        path = []
        if ends[0]: path.append({"name": ends[0]["name"], "lat": ends[0]["lat"], "lon": ends[0]["lon"], "interchange": True})
        for c in chain:
            s = next(s for s in sites if s["id"] == c)
            s["type"] = "line"
            path.append({"name": f"New: {s['area'] or 'site ' + str(c)}", "lat": s["lat"], "lon": s["lon"], "site": c})
        if ends[1]: path.append({"name": ends[1]["name"], "lat": ends[1]["lat"], "lon": ends[1]["lon"], "interchange": True})
        length = sum(math.hypot(*(a - b for a, b in zip(xy(p["lat"], p["lon"]), xy(q["lat"], q["lon"])))) for p, q in zip(path, path[1:]))
        members = [s for s in sites if s["id"] in chain]
        relieved = defaultdict(float)
        for s in members:
            for r in s["relieves"]:
                relieved[r["station"]] += r["residents"]
        res = sum(s["new_residents"] for s in members)
        lines.append({"name": f"Proposed Line {next(letters)}", "stations": path, "new_stations": len(chain),
                      "length_km": round(length / 1000, 1), "new_residents": res,
                      "residents_per_km": round(res / max(length / 1000, 0.5)),
                      "relieves": [{"station": k, "residents": round(v)} for k, v in sorted(relieved.items(), key=lambda kv: -kv[1])[:4]],
                      "why": ""})
    lines.sort(key=lambda l: -l["new_residents"])
    ins_ps = (load("insights.json", {}) or {}).get("per_station", {})
    for i, l in enumerate(lines):
        l["name"] = f"Proposed Line {chr(65 + i)}"
        areas = ", ".join(p["name"][5:] for p in l["stations"] if p.get("site"))
        ic = " and ".join(p["name"] for p in l["stations"] if p.get("interchange"))
        rl = ", ".join(r["station"] for r in l["relieves"][:2])
        pressure = None
        if ins_ps and l["relieves"]:
            nm2sid = {st_["name"]: st_["id"] for st_ in stations}
            cands = [(r["station"], ins_ps.get(str(nm2sid.get(r["station"])), {}).get("am_track_load", 0)) for r in l["relieves"][:2]]
            best = max(cands, key=lambda c: c[1])
            pressure = best if best[1] > 0 else None
        l["why"] = (f"{l['new_stations']} new station(s) serving {l['new_residents']:,} residents who are more than a 10-minute walk "
                    f"from any existing or planned MRT station ({areas}). Interchanges at {ic or 'n/a'}."
                    + (f" Takes pressure off {rl}." if rl else "")
                    + (f" The busiest track next to {pressure[0]} carries ~{pressure[1]:,} people at 7-9am." if pressure else ""))

    out = {
        "generated": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="minutes"),
        "params": P,
        "inputs": {"hdb_blocks": len(blocks), "future_projects": len(future), "lta_passenger_volume": has_pv, "lta_crowd_forecast": has_fc, "bus_feeder_areas": len(feeders)},
        "summary": {"residents_now": round(tot_now), "future_residents": round(tot_future),
                    "unserved_now": round(un_now), "unserved_after_lta_plans": round(un_after_plans),
                    "served_by_planned_lines": round(sum(fixed_by_plan.values()))},
        "fixed_by_lta_plans": [{"station": k, "residents": round(v)} for k, v in sorted(fixed_by_plan.items(), key=lambda kv: -kv[1])],
        "gaps": sorted(gaps, key=lambda g: -g[3]),
        "sites": sites, "infill": infill, "lines": lines,
        "future": [{"type": f["type"], "name": f["name"], "town": f["town"], "units": int(f["units"]), "launch": f["launch"],
                    "lat": float(f["lat"]), "lon": float(f["lon"]), "source": f["source"]} for f in future],
    }
    json.dump(out, open(f"{D}/analysis.json", "w"), separators=(",", ":"))
    print(json.dumps(out["summary"]), f"| {len(sites)} sites, {len(lines)} lines, {len(infill)} infill")


if __name__ == "__main__":
    main()
