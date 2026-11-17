"""Home prices vs the MRT -> docs/data/prices.json

  HDB resale (data/raw/prices/hdb_resale.csv) placed on the map by block + street (data/raw/hdb_block_coords.json)
  Private condo / EC / landed (data/raw/prices/ura_transactions.json, if a URA key has been added)

Outputs
  hdb_blocks / private_projects   median $ per sq ft over the last 12 months + change vs the 12 months before
  towns_monthly                   median HDB $psf per town per month (for the compare page)
  mrt_premium                     how $psf changes with distance to the nearest MRT station, relative to the town's median
  new_station_effect              did prices near newly opened stations rise faster than elsewhere in the same towns?
  proposed_lines                  homes within a 10-minute walk of each proposed new station
"""
import csv, json, math, os, statistics, datetime as dt
from collections import defaultdict

SGT = dt.timezone(dt.timedelta(hours=8))
SQM_TO_SQFT = 10.7639
# Recent openings, used for the before/after test (LTA opening dates)
OPENINGS = [
    {"name": "Thomson-East Coast Line stage 2", "date": "2021-08", "codes": ["TE4", "TE5", "TE6", "TE7", "TE8", "TE9"]},
    {"name": "Thomson-East Coast Line stage 3", "date": "2022-11", "codes": ["TE10", "TE11", "TE12", "TE13", "TE14", "TE15", "TE16", "TE17", "TE18", "TE19", "TE20", "TE22"]},
    {"name": "Thomson-East Coast Line stage 4", "date": "2024-06", "codes": ["TE23", "TE24", "TE25", "TE26", "TE27", "TE28", "TE29"]},
    {"name": "Hume (Downtown Line)", "date": "2025-02", "codes": ["DT4"]},
]


def km(a, b):
    return math.hypot((a[1] - b[1]) * 111.32 * math.cos(math.radians(1.35)), (a[0] - b[0]) * 110.57)


def med(v):
    return statistics.median(v) if v else None


def mshift(m, k):
    y, mo = int(m[:4]), int(m[5:7]) + k
    while mo <= 0: y, mo = y - 1, mo + 12
    while mo > 12: y, mo = y + 1, mo - 12
    return f"{y}-{mo:02d}"


AMENITIES = [  # (key in points.json, label, radius km, how it enters the model)
    ("primary_schools", "Primary school within 1 km", 1.0, "count3"),
    ("secondary_schools", "Secondary school within 1 km", 1.0, "any"),
    ("hawker", "Hawker centre within 500 m", 0.5, "any"),
    ("supermarkets", "Supermarket within 400 m", 0.4, "any"),
    ("clinics", "GP clinics within 400 m", 0.4, "count5"),
    ("polyclinics", "Polyclinic within 1 km", 1.0, "any"),
    ("hospitals", "Public hospital within 2 km", 2.0, "any"),
    ("parks", "Park within 400 m", 0.4, "any"),
]


def amenity_model(sales, m12, near_cache, km):
    """What buyers pay for: $psf on each amenity, holding distance to the CBD, flat age, size and MRT distance fixed."""
    path = "data/raw/amenities/points.json"
    if not os.path.exists(path):
        return None
    import numpy as np
    pts = json.load(open(path))
    use = [a for a in AMENITIES if len(pts.get(a[0], [])) >= 5]
    recent = [s for s in sales if s["month"] >= m12]
    keys = sorted({s["k"] for s in recent})
    kpos = {k: i for i, k in enumerate(keys)}
    first = {}
    for s in recent:
        first.setdefault(s["k"], (s["lat"], s["lon"]))
    lat = np.array([first[k][0] for k in keys]); lon = np.array([first[k][1] for k in keys])
    kx = 111.32 * math.cos(math.radians(1.35)); ky = 110.57
    feat = {}
    for key, label, r, kind in use:
        P = np.array([[p[0], p[1]] for p in pts[key]])
        cnt = np.zeros(len(keys)); dmin = np.full(len(keys), 99.0)
        for i in range(0, len(keys), 500):
            d = np.hypot((lon[i:i + 500, None] - P[None, :, 1]) * kx, (lat[i:i + 500, None] - P[None, :, 0]) * ky)
            cnt[i:i + 500] = (d <= r).sum(1); dmin[i:i + 500] = d.min(1)
        feat[key] = np.minimum(cnt, 3) if kind == "count3" else np.minimum(cnt, 5) if kind == "count5" else (cnt > 0).astype(float)
        feat[key + "_dmin"] = dmin
    RP = (1.28393, 103.85143)
    cbd = np.hypot((lon - RP[1]) * kx, (lat - RP[0]) * ky)
    rows, y = [], []
    for s in recent:
        i = kpos[s["k"]]
        sqm = s["price"] / s["psf"] / SQM_TO_SQFT
        rows.append([1.0, cbd[i], s["lease"], sqm, min(near_cache[s["k"]][0], 2.0)] + [feat[a[0]][i] for a in use])
        y.append(s["psf"])
    X, y = np.array(rows), np.array(y)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    # standard errors clustered by block (sales in the same block are not independent)
    blk = np.array([kpos[s["k"]] for s in recent])
    XtX_inv = np.linalg.inv(X.T @ X)
    G = np.zeros((len(keys), X.shape[1])); np.add.at(G, blk, X * resid[:, None]); meat = G.T @ G
    se = np.sqrt(np.diag(XtX_inv @ meat @ XtX_inv))
    r2 = 1 - (resid ** 2).sum() / ((y - y.mean()) ** 2).sum()
    per_km = beta[1]
    out = {"sales": len(y), "blocks": len(keys), "r2": round(float(r2), 2), "mean_psf": round(float(y.mean())),
           "controls": {"per_km_from_cbd": round(float(per_km), 1), "per_year_newer": round(float(beta[2]), 1),
                        "per_sqm_bigger": round(float(beta[3]), 2), "per_km_from_mrt": round(float(beta[4]), 1)},
           "effects": [], "coverage": [], "places": {a[0]: len(pts.get(a[0], [])) for a in AMENITIES}}
    for j, (key, label, r, kind) in enumerate(use):
        b, e = float(beta[5 + j]), float(se[5 + j])
        out["effects"].append({"key": key, "label": label + (" (each, up to 3)" if kind == "count3" else " (each, up to 5)" if kind == "count5" else ""),
                               "psf": round(b, 1), "se": round(e, 1), "sure": abs(b) > 2 * e,
                               "km_cbd_equiv": round(b / -per_km, 2) if per_km < 0 else None,
                               "share_of_blocks": round(100 * float((feat[key] > 0).mean()))})
    for lo, hi, nm in [(0, 5, "within 5 km of the CBD"), (5, 10, "5–10 km"), (10, 15, "10–15 km"), (15, 99, "over 15 km")]:
        m = (cbd >= lo) & (cbd < hi)
        if m.sum() < 20:
            continue
        out["coverage"].append({"band": nm, "blocks": int(m.sum()),
                                **{a[0]: round(100 * float((feat[a[0]][m] > 0).mean())) for a in use}})
    print(f"amenities: {len(use)} types, {len(y)} sales, R2 {r2:.2f}")
    return out


def grants_summary(sales_by_fy):
    p = "data/raw/grants/cpf_housing_grants.json"
    if not os.path.exists(p):
        return None
    G = json.load(open(p))
    near, other_near, total = defaultdict(int), defaultdict(int), defaultdict(int)
    types = defaultdict(lambda: defaultdict(int))
    num = lambda v: int(float(v)) if str(v).replace(".", "", 1).isdigit() else 0
    for r in G["records"]:
        fy = str(r.get("financial_year", "")).strip(); sch = r.get("housing_grant_scheme", ""); ty = r.get("type_of_grant", ""); n = num(r.get("no_of_hh"))
        if not fy.isdigit():
            continue
        label = f"{sch} – {ty}" if ty and ty.lower() != "na" else sch
        types[label][fy] += n
        total[fy] += n
        txt = f"{sch} {ty}".lower()
        if "proximity" in txt:
            near[fy] += n
        elif "near" in txt or "living with" in txt:
            other_near[fy] += n            # older CPF Housing Grant variants for living with/near parents (before the PHG)
    years = sorted(total)
    for y in years:
        if not near[y]:
            near[y] = other_near[y]
    return {"source": G["source"], "years": years,
            "near_or_with_family": {y: near[y] for y in years},
            "resale_sales": {y: sales_by_fy.get(y) for y in years},
            "share_of_resale_pct": {y: round(100 * near[y] / sales_by_fy[y], 1) for y in years if sales_by_fy.get(y) and near[y]},
            "by_type": {k: dict(v) for k, v in sorted(types.items())}}


def main():
    st = json.load(open("docs/data/stations.json"))
    now_year = dt.date.today().year + dt.date.today().timetuple().tm_yday / 366
    mrt = [s for s in st["stations"] if s["mode"] == "MRT" and s["open_year"] <= now_year]
    code2s = {c["code"]: s for s in st["stations"] for c in s["codes"]}

    def nearest(lat, lon, pool=mrt, skip=()):
        best = (99, None)
        for s in pool:
            if s["id"] in skip:
                continue
            d = km((lat, lon), (s["lat"], s["lon"]))
            if d < best[0]:
                best = (d, s)
        return best

    out = {"generated": dt.datetime.now(SGT).isoformat(timespec="minutes"), "hdb": None, "private": None}

    # ------------------------------------------------------------ HDB resale
    coords = json.load(open("data/raw/hdb_block_coords.json")) if os.path.exists("data/raw/hdb_block_coords.json") else {}
    path = "data/raw/prices/hdb_resale.csv"
    if coords and os.path.exists(path):
        sales, miss = [], 0
        near_cache = {}
        for r in csv.DictReader(open(path, encoding="utf-8")):
            k = f"{r['block'].strip().upper()}|{r['street_name'].strip().upper()}"
            c = coords.get(k)
            if not c:
                miss += 1; continue
            psf = float(r["resale_price"]) / (float(r["floor_area_sqm"]) * SQM_TO_SQFT)
            if k not in near_cache:
                near_cache[k] = nearest(c[0], c[1])
            sales.append({"k": k, "lat": c[0], "lon": c[1], "town": r["town"].title(), "month": r["month"], "type": r["flat_type"],
                          "psf": psf, "price": float(r["resale_price"]), "lease": int(r["lease_commence_date"] or 0)})
        this_month = dt.date.today().strftime("%Y-%m")
        sales = [s for s in sales if s["month"] < this_month]          # skip the month still being registered
        last = max(s["month"] for s in sales)
        m12, m24 = mshift(last, -11), mshift(last, -23)
        # town-month medians (for compare page and for "relative to town" measures)
        tm = defaultdict(list)
        for s in sales:
            tm[(s["town"], s["month"])].append(s["psf"])
        tmm = {k: med(v) for k, v in tm.items()}
        towns_monthly = defaultdict(dict)
        for (t, m), v in tm.items():
            towns_monthly[t][m] = round(med(v))
        all_monthly = defaultdict(list)
        for s in sales:
            all_monthly[s["month"]].append(s["psf"])
        towns_monthly["All HDB"] = {m: round(med(v)) for m, v in all_monthly.items()}
        town12 = defaultdict(list)
        for s in sales:
            if s["month"] >= m12:
                town12[s["town"]].append(s["psf"])
        town12 = {t: med(v) for t, v in town12.items()}
        # per block
        blk = defaultdict(lambda: {"now": [], "before": [], "prices": []})
        for s in sales:
            if s["month"] >= m12:
                blk[s["k"]]["now"].append(s["psf"]); blk[s["k"]]["prices"].append(s["price"])
            elif s["month"] >= m24:
                blk[s["k"]]["before"].append(s["psf"])
        blocks = []
        for k, v in blk.items():
            if len(v["now"]) < 2:
                continue
            c = coords[k]; a, b = med(v["now"]), med(v["before"])
            trend = round(100 * (a - b) / b, 1) if b and len(v["before"]) >= 2 else None
            d, s = near_cache[k]
            blocks.append([c[0], c[1], round(a), len(v["now"]), trend, c[2], k.replace("|", " "), round(med(v["prices"])), round(d * 1000), s["name"] if s else ""])
        # MRT premium: each sale's $psf vs its town's 12-month median, by distance band (last 24 months)
        bands = [(0, 200), (200, 400), (400, 600), (600, 800), (800, 1000), (1000, 1300), (1300, 1700), (1700, 9999)]
        rel = defaultdict(list); abs_ = defaultdict(list)
        for s in sales:
            if s["month"] < m24 or s["town"] not in town12:
                continue
            dm = near_cache[s["k"]][0] * 1000
            for lo, hi in bands:
                if lo <= dm < hi:
                    rel[(lo, hi)].append(s["psf"] / town12[s["town"]]); abs_[(lo, hi)].append(s["psf"]); break
        premium = [{"band_m": f"{lo}–{hi}" if hi < 9999 else f"{lo}+", "relative_to_town_pct": round(100 * (med(rel[(lo, hi)]) - 1), 1) if rel[(lo, hi)] else None,
                    "median_psf": round(med(abs_[(lo, hi)])) if abs_[(lo, hi)] else None, "sales": len(rel[(lo, hi)])} for lo, hi in bands]
        # new-station before/after: blocks whose nearest station went from >800 m to <600 m when the line opened
        effects = []
        for op in OPENINGS:
            new_ids = {code2s[c]["id"] for c in op["codes"] if c in code2s}
            new_st = [s for s in mrt if s["id"] in new_ids]
            if not new_st:
                continue
            treated, control_towns = set(), set()
            for k in near_cache:
                c = coords[k]
                dn, _ = nearest(c[0], c[1], new_st)
                do, _ = nearest(c[0], c[1], skip=new_ids)
                if dn < 0.6 and do > 0.8:
                    treated.add(k); control_towns.add(c[2].title())
            if len(treated) < 5:
                continue
            before = (mshift(op["date"], -24), mshift(op["date"], -1)); after = (mshift(op["date"], 1), mshift(op["date"], 24))
            def ratio(keys_ok, lo, hi):
                v = [s["psf"] / tmm[(s["town"], s["month"])] for s in sales if lo <= s["month"] <= hi and keys_ok(s)]
                return med(v), len(v)
            is_t = lambda s: s["k"] in treated
            is_c = lambda s: s["k"] not in treated and s["town"] in control_towns and near_cache[s["k"]][0] > 0.8
            tb, ntb = ratio(is_t, *before); ta, nta = ratio(is_t, *after)
            cb, ncb = ratio(is_c, *before); ca, nca = ratio(is_c, *after)
            if None in (tb, ta, cb, ca):
                continue
            effects.append({"opening": op["name"], "opened": op["date"], "blocks_gaining_station": len(treated), "towns": sorted(control_towns),
                            "near_new_station_change_pct": round(100 * (ta / tb - 1), 1), "rest_of_town_change_pct": round(100 * (ca / cb - 1), 1),
                            "difference_pts": round(100 * ((ta / tb) - (ca / cb)), 1), "sales": [ntb, nta, ncb, nca],
                            "after_window_complete": after[1] <= last, "low_confidence": min(ntb, nta, ncb, nca) < 100})
        # why towns differ: town median $psf vs distance to Raffles Place and flat age (least squares, 26 towns)
        RP = (1.28393, 103.85143)
        tw = defaultdict(lambda: {"psf": [], "year": [], "cbd": [], "near": [], "sqm": []})
        for s in sales:
            if s["month"] < m12:
                continue
            t = tw[s["town"]]; t["psf"].append(s["psf"]); t["year"].append(s["lease"]); t["cbd"].append(km((s["lat"], s["lon"]), RP))
            t["near"].append(near_cache[s["k"]][0] < 0.4); t["sqm"].append(s["price"] / s["psf"] / SQM_TO_SQFT)
        trows = [{"town": k, "psf": med(v["psf"]), "built": med(v["year"]), "km_cbd": med(v["cbd"]), "near_mrt_pct": 100 * sum(v["near"]) / len(v["near"]),
                  "sqm": med(v["sqm"]), "sales": len(v["psf"])} for k, v in tw.items() if len(v["psf"]) >= 100]
        X = [[1, r["km_cbd"], r["built"]] for r in trows]; y = [r["psf"] for r in trows]
        n = 3; A = [[sum(x[i] * x[j] for x in X) for j in range(n)] for i in range(n)]; b = [sum(x[i] * yy for x, yy in zip(X, y)) for i in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                f = A[j][i] / A[i][i]; A[j] = [a - f * c for a, c in zip(A[j], A[i])]; b[j] -= f * b[i]
        coef = [0.0] * n
        for i in reversed(range(n)):
            coef[i] = (b[i] - sum(A[i][j] * coef[j] for j in range(i + 1, n))) / A[i][i]
        ym = sum(y) / len(y); ssr = 0.0
        for r, x in zip(trows, X):
            r["predicted"] = sum(a * c for a, c in zip(x, coef)); ssr += (r["psf"] - r["predicted"]) ** 2
        r2 = 1 - ssr / sum((v - ym) ** 2 for v in y)
        why = {"per_km_from_cbd": round(coef[1], 1), "per_year_newer": round(coef[2], 1), "r2": round(r2, 2),
               "towns": [{"town": r["town"], "psf": round(r["psf"]), "predicted": round(r["predicted"]), "gap": round(r["psf"] - r["predicted"]),
                          "built": round(r["built"]), "km_cbd": round(r["km_cbd"], 1), "near_mrt_pct": round(r["near_mrt_pct"]), "sqm": round(r["sqm"]), "sales": r["sales"]}
                         for r in sorted(trows, key=lambda r: -r["psf"])]}
        amen = amenity_model(sales, m12, near_cache, km)
        out["hdb"] = {"town_why": why, "amenities": amen, "latest_month": last, "sales_used": len(sales), "sales_unplaced": miss, "blocks": blocks,
                      "towns_monthly": towns_monthly, "town_median_psf_12m": {t: round(v) for t, v in sorted(town12.items(), key=lambda kv: -kv[1])},
                      "mrt_premium": premium, "new_station_effect": effects}
        fy, fy_months = defaultdict(int), defaultdict(set)
        for r in csv.DictReader(open(path, encoding="utf-8")):
            y, mo = int(r["month"][:4]), int(r["month"][5:7])
            k = str(y if mo >= 4 else y - 1)            # HDB financial year starts in April
            fy[k] += 1; fy_months[k].add(r["month"])
        out["hdb"]["grants"] = grants_summary({k: v for k, v in fy.items() if len(fy_months[k]) == 12})
        print(f"HDB: {len(sales)} sales placed ({miss} not matched), {len(blocks)} blocks, latest {last}")

    # ------------------------------------------------------------ URA private
    up = "data/raw/prices/ura_transactions.json"
    if os.path.exists(up):
        tx = json.load(open(up))["tx"].values()
        def month_of(c):   # URA contractDate is MMYY
            return f"20{c[2:4]}-{c[0:2]}" if c and len(c) == 4 else None
        rows = [t for t in tx if t["lat"] and t["area_sqm"] > 0 and t["price"] > 0 and month_of(t["contract"])]
        if rows:
            last = max(month_of(t["contract"]) for t in rows); m12, m24 = mshift(last, -11), mshift(last, -23)
            proj = defaultdict(lambda: {"now": [], "before": [], "meta": None})
            for t in rows:
                m = month_of(t["contract"]); psf = t["price"] / t["units"] / (t["area_sqm"] * SQM_TO_SQFT) if t["units"] else 0
                p = proj[(t["project"], t["street"])]; p["meta"] = t
                if m >= m12: p["now"].append(psf)
                elif m >= m24: p["before"].append(psf)
            projects = []
            for (name, street), p in proj.items():
                if not p["now"]:
                    continue
                t = p["meta"]; a, b = med(p["now"]), med(p["before"])
                d, s = nearest(t["lat"], t["lon"])
                projects.append([t["lat"], t["lon"], round(a), len(p["now"]), round(100 * (a - b) / b, 1) if b else None, name, t["type"], t["segment"], round(d * 1000), s["name"] if s else ""])
            seg_monthly = defaultdict(lambda: defaultdict(list))
            for t in rows:
                psf = t["price"] / t["units"] / (t["area_sqm"] * SQM_TO_SQFT) if t["units"] else 0
                seg_monthly[t["segment"] or "?"][month_of(t["contract"])].append(psf)
            out["private"] = {"latest_month": last, "transactions": len(rows), "projects": projects,
                              "segments_monthly": {k: {m: round(med(v)) for m, v in mm.items()} for k, mm in seg_monthly.items()}}
            print(f"Private: {len(rows)} transactions, {len(projects)} projects sold in the last 12 months")

    # ------------------------------------------------------------ proposed lines: who would gain a station
    an = json.load(open("docs/data/analysis.json")) if os.path.exists("docs/data/analysis.json") else {}
    pl = []
    for l in an.get("lines", []) + [{"name": "Single new stations", "stations": [{"lat": s["lat"], "lon": s["lon"], "site": 1} for s in an.get("infill", [])]}]:
        sites = [p for p in l["stations"] if p.get("site")]
        hb = [b for b in (out["hdb"] or {}).get("blocks", []) if any(km((b[0], b[1]), (p["lat"], p["lon"])) < 0.8 for p in sites)]
        pp = [p for p in (out["private"] or {}).get("projects", []) if any(km((p[0], p[1]), (q["lat"], q["lon"])) < 0.8 for q in sites)]
        pl.append({"line": l["name"], "hdb_blocks_with_sales": len(hb), "hdb_median_psf": round(med([b[2] for b in hb])) if hb else None,
                   "private_projects": len(pp), "private_median_psf": round(med([p[2] for p in pp])) if pp else None})
    out["proposed_lines"] = pl
    json.dump(out, open("docs/data/prices.json", "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
