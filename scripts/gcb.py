"""Good Class Bungalow (GCB) sales, built from the URA private-sales file the Data bot already keeps.

No extra URA calls: reads data/raw/prices/ura_transactions.json (fetch_prices.py refreshes it weekly).
GCB areas: URA's 39 gazetted Good Class Bungalow Area polygons from data.gov.sg (free, no key),
cached in data/raw/gcba.geojson and refreshed every 90 days.

A sale counts when it is a Detached house (not strata) whose URA location falls inside a GCBA polygon,
or within EDGE_M metres of one with at least 1,400 m2 of land (URA gives one point per street, not per plot).
Some URA records come with no location at all (incl. big deals like Peirce Road): for those the street is looked up on
OneMap (free, no key); the street counts when at least half of its OneMap addresses fall inside one GCB area.
OneMap points are cached forever in data/raw/gcb_street_points.json.
Uncaveated sales (e.g. bought through trusts) never reach URA's data, so the list is a floor, not a full count.
Writes docs/data/gcb.json for docs/gcb.html.
"""
import datetime as dt, html, json, math, os, re, statistics, sys, time
import requests

RAW = "data/raw"
GCBA_ID = "d_6bdaba5f6cff443f4b2bc2e28a98f383"
GCBA_PATH = f"{RAW}/gcba.geojson"
STREETS_PATH = f"{RAW}/gcb_street_points.json"
TX_PATH = f"{RAW}/prices/ura_transactions.json"
OUT = "docs/data/gcb.json"
EDGE_M = 150
SQM_TO_SQFT = 10.7639


def fresh(path, days):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < days * 86400


def poll_download(did):
    j = {}
    for _ in range(12):
        j = requests.get(f"https://api-open.data.gov.sg/v1/public/api/datasets/{did}/poll-download", timeout=60).json()
        if j.get("code") == 0 and j.get("data", {}).get("url"):
            return requests.get(j["data"]["url"], timeout=300).text
        time.sleep(10)
    raise RuntimeError(f"{did}: {j}")


def area_name(props):
    for k, v in props.items():
        if k.upper() in ("NAME", "GCBA_NAME", "DESCRIPTION_NAME") and v and "<" not in str(v):
            return str(v).strip()
    d = props.get("Description") or props.get("description") or ""
    m = re.search(r"<th>NAME</th>\s*<td>(.*?)</td>", d, re.I)
    return html.unescape(m.group(1).strip()) if m else ""


def load_areas():
    if not fresh(GCBA_PATH, 90):
        try:
            txt = poll_download(GCBA_ID)
            json.loads(txt)
            os.makedirs(RAW, exist_ok=True)
            open(GCBA_PATH, "w").write(txt)
            print("GCBA: polygons downloaded")
        except Exception as e:
            if not os.path.exists(GCBA_PATH):
                sys.exit(f"GCBA polygons unavailable: {e}")
            print(f"GCBA: download failed ({e}), reusing saved file")
    gj = json.load(open(GCBA_PATH))
    areas = []
    for f in gj["features"]:
        g = f.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
        rings = [[(p[0], p[1]) for p in poly[0]] for poly in polys if poly]
        if rings:
            name = area_name(f.get("properties", {})).title().replace("Gcba", "").strip(" -") or "GCB area"
            areas.append({"name": name, "rings": rings})
    print(f"GCBA: {len(areas)} polygons")
    return areas


def inside(lon, lat, ring):
    c = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            c = not c
    return c


def dist_m(lon, lat, ring):
    kx, ky = 111320 * math.cos(math.radians(lat)), 110540
    best = 1e12
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        ax, ay, bx, by = (x1 - lon) * kx, (y1 - lat) * ky, (x2 - lon) * kx, (y2 - lat) * ky
        dx, dy = bx - ax, by - ay
        t = 0 if dx == dy == 0 else max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def street_points(streets):
    cache = json.load(open(STREETS_PATH)) if os.path.exists(STREETS_PATH) else {}
    todo = [st for st in streets if st not in cache]
    for st in todo:
        pts = []
        try:
            for page in (1, 2, 3):
                r = requests.get("https://www.onemap.gov.sg/api/common/elastic/search",
                                 params={"searchVal": st, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": page}, timeout=30).json()
                for x in r.get("results", []):
                    if (x.get("ROAD_NAME") or "").upper() == st:
                        pts.append([round(float(x["LONGITUDE"]), 6), round(float(x["LATITUDE"]), 6)])
                if page >= int(r.get("totalNumPages") or 1):
                    break
                time.sleep(0.3)
            cache[st] = pts
        except Exception as e:
            print(f"OneMap {st}: {e}")
        time.sleep(0.3)
    if todo:
        json.dump(cache, open(STREETS_PATH, "w"), separators=(",", ":"), sort_keys=True)
        print(f"OneMap: looked up {len(todo)} streets")
    return cache


def match_street(t, areas, pts_by_street):
    pts = [p for st in re.split(r"[\\/]", t["street"]) for p in pts_by_street.get(st.strip(), [])]
    if not pts:
        return None, None
    hits = {}
    for lon, lat in pts:
        for a in areas:
            if any(inside(lon, lat, r) for r in a["rings"]):
                hits[a["name"]] = hits.get(a["name"], 0) + 1
                break
    if hits:
        name, k = max(hits.items(), key=lambda x: x[1])
        if k / len(pts) >= 0.5:
            return name, "street"
    if t["area_sqm"] >= 1400:
        d, name = min((min(dist_m(lon, lat, r) for r in a["rings"] for lon, lat in pts), a["name"]) for a in areas)
        if d <= EDGE_M:
            return name, "edge"
    return None, None


def match(t, areas):
    lon, lat = t.get("lon"), t.get("lat")
    if lon is None or lat is None:
        return None, None
    for a in areas:
        if any(inside(lon, lat, r) for r in a["rings"]):
            return a["name"], "inside"
    if t["area_sqm"] >= 1400:
        d, name = min((min(dist_m(lon, lat, r) for r in a["rings"]), a["name"]) for a in areas)
        if d <= EDGE_M:
            return name, "edge"
    return None, None


def main():
    areas = load_areas()
    store = json.load(open(TX_PATH))
    det = [t for t in store["tx"].values() if t.get("type") == "Detached" and t.get("contract") and t.get("price")]
    no_xy = sorted({st.strip() for t in det if t.get("lat") is None for st in re.split(r"[\\/]", t["street"])})
    pts_by_street = street_points(no_xy)
    rows = []
    for t in det:
        name, how = match(t, areas) if t.get("lat") is not None else match_street(t, areas, pts_by_street)
        if not name:
            continue
        mm, yy = t["contract"][:2], t["contract"][2:]
        sqft = t["area_sqm"] * SQM_TO_SQFT
        rows.append({"ym": f"20{yy}-{mm}", "street": t["street"].title(), "area": name, "district": t["district"],
                     "land_sqm": round(t["area_sqm"]), "land_sqft": round(sqft), "price": t["price"],
                     "psf": round(t["price"] / sqft) if sqft else None, "tenure": t["tenure"],
                     "sale": {"1": "New sale", "2": "Sub sale", "3": "Resale"}.get(str(t.get("sale")), ""),
                     "match": how, "small": t["area_sqm"] < 1400})
    rows.sort(key=lambda r: r["ym"], reverse=True)

    def q(ym):
        y, m = ym.split("-")
        return f"{y} Q{(int(m) - 1) // 3 + 1}"
    quarters = {}
    for r in rows:
        quarters.setdefault(q(r["ym"]), []).append(r)
    qstats = [{"q": k, "n": len(v), "value": sum(r["price"] for r in v),
               "median_psf": round(statistics.median(r["psf"] for r in v if r["psf"])),
               "median_price": statistics.median(r["price"] for r in v)} for k, v in sorted(quarters.items())]
    by_area = {}
    for r in rows:
        by_area.setdefault(r["area"], []).append(r)
    astats = [{"area": k, "n": len(v), "value": sum(r["price"] for r in v),
               "median_psf": round(statistics.median(r["psf"] for r in v if r["psf"])),
               "top": max(r["price"] for r in v), "last": max(r["ym"] for r in v)} for k, v in by_area.items()]
    astats.sort(key=lambda a: -a["n"])
    years = {}
    for r in rows:
        y = years.setdefault(r["ym"][:4], {"year": r["ym"][:4], "n": 0, "value": 0, "psf": []})
        y["n"] += 1; y["value"] += r["price"]; y["psf"].append(r["psf"])
    ystats = [{"year": y["year"], "n": y["n"], "value": y["value"], "median_psf": round(statistics.median(y["psf"]))}
              for y in sorted(years.values(), key=lambda y: y["year"])]

    out = {"updated": dt.date.today().isoformat(), "ura_updated": store.get("updated"), "areas_total": len(areas),
           "edge_m": EDGE_M, "sales": rows, "quarters": qstats, "years": ystats, "areas": astats}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"GCB: {len(rows)} sales ({sum(r['match'] == 'edge' for r in rows)} edge matches) across {len(by_area)} areas, "
          f"{rows[-1]['ym'] if rows else '-'} to {rows[0]['ym'] if rows else '-'}")


if __name__ == "__main__":
    main()
