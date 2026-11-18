"""Punggol & Sengkang vs Tengah: do new towns' prices follow the same path? -> docs/data/newtowns.json (rendered by docs/newtowns.html)

Sources (all HDB via data.gov.sg): resale transactions 1990-today, BTO launch price ranges FY2008-FY2024, HDB Resale Price Index.
  1. Start point: BTO launch prices, as a share of the whole HDB resale market that year (so 2008 and 2023 compare fairly).
  2. Path: each town's resale $psf relative to the all-HDB median, by years since the town's first busy resale year.
  3. BTO -> MOP gain: what 4-room BTO buyers of each launch year got when their flats hit MOP 5 years after completion.
  4. Tengah early signal: Bukit Batok / Choa Chu Kang blocks next to Tengah vs the rest of their towns since Tengah's first keys (Aug 2023).
"""
import csv, json, math, os, statistics as stx, datetime as dt
from collections import defaultdict

SQFT = 10.7639
RAW = "data/raw/prices"
TOWNS = ["Sengkang", "Punggol", "Tengah"]
FOUNDED = {"Sengkang": 1995, "Punggol": 2000, "Tengah": 2018}   # flats leased before this belong to the old rural estate, not the new town
TYPICAL_SQM = {"2-room": 46, "3-room": 68, "4-room": 93, "5-room": 113}   # typical BTO floor areas, to put BTO prices in $psf
MILESTONES = {
    "Sengkang": [(1997, "first flats completed (approx.)"), (2003, "North East Line and Sengkang LRT open")],
    "Punggol": [(2003, "North East Line opens at Punggol"), (2005, "Punggol LRT East loop opens"), (2011, "Punggol Waterway opens"),
                (2014, "Punggol LRT West loop opens"), (2016, "Waterway Point mall opens"), (2024, "Punggol Coast station opens")],
    "Tengah": [(2018, "first BTO launch"), (2023, "first keys (Aug)"), (2028, "first flats reach MOP; Jurong Region Line Tengah station due"),
               (2029, "Tengah Park / Tengah Plantation stations due")],
}
med = lambda v: stx.median(v) if v else None
SGT = dt.timezone(dt.timedelta(hours=8))


def km(a, b):
    return math.hypot((a[1] - b[1]) * 111.32 * math.cos(math.radians(1.35)), (a[0] - b[0]) * 110.57)


def main():
    hp = f"{RAW}/history_newtowns.json"
    if not os.path.exists(hp) or not os.path.exists(f"{RAW}/hdb_resale.csv"):
        print("newtowns: history not downloaded yet"); return
    H = json.load(open(hp))
    sales = [{"month": r[0], "town": r[1].title(), "type": r[2].replace(" ROOM", "-room").title().replace("-Room", "-room"), "lease": int(r[6]) if str(r[6]).isdigit() else None,
              "price": r[7], "psf": r[7] / (r[5] * SQFT), "blk": r[3], "street": r[4]} for r in H["rows"]]
    allm = {m: [v] for m, v in H["all_hdb_median_psf"].items()}      # 1990-2016 monthly medians
    cur = defaultdict(list)
    this_month = dt.date.today().strftime("%Y-%m")
    for r in csv.DictReader(open(f"{RAW}/hdb_resale.csv", encoding="utf-8")):
        if r["month"] >= this_month:
            continue
        a, p = float(r["floor_area_sqm"]), float(r["resale_price"])
        psf = p / (a * SQFT); cur[r["month"]].append(psf)
        if r["town"].title() in TOWNS:
            sales.append({"month": r["month"], "town": r["town"].title(), "type": r["flat_type"].replace(" ROOM", "-room").title().replace("-Room", "-room"),
                          "lease": int(r["lease_commence_date"]), "price": p, "psf": psf, "blk": r["block"], "street": r["street_name"]})
    market = {m: v[0] for m, v in allm.items()}
    market.update({m: med(v) for m, v in cur.items()})
    last = max(market)
    year_mkt = defaultdict(list)
    for m, v in market.items():
        year_mkt[int(m[:4])].append(v)
    year_mkt = {y: med(v) for y, v in year_mkt.items()}

    # ---- 2. paths: yearly median $psf and relative to market, years since the first year with >= 200 resales
    out_towns = {}
    for t in TOWNS:
        ts = [s for s in sales if s["town"] == t and (s["lease"] or 0) >= FOUNDED[t]]
        by_year, rel_year, n_year = defaultdict(list), defaultdict(list), defaultdict(int)
        monthly = defaultdict(list)
        for s in ts:
            y = int(s["month"][:4]); by_year[y].append(s["psf"]); n_year[y] += 1
            rel_year[y].append(s["psf"] / market[s["month"]]); monthly[s["month"]].append(s["psf"])
        started = [y for y in sorted(n_year) if n_year[y] >= 10]       # year 0 = first year with new-town resales (10+ to skip stray records)
        y0 = started[0] if started else None
        out_towns[t] = {"year0": y0, "first_resale_month": min((s["month"] for s in ts), default=None), "excluded_old_flats": sum(1 for s in sales if s["town"] == t and (s["lease"] or 0) < FOUNDED[t]),
                        "years": {y: {"psf": round(med(by_year[y])), "rel": round(med(rel_year[y]), 3), "n": n_year[y]} for y in sorted(by_year) if n_year[y] >= 10},
                        "monthly_psf": {m: round(med(v)) for m, v in sorted(monthly.items()) if len(v) >= 5},
                        "milestones": MILESTONES.get(t, [])}
    if not out_towns["Tengah"]["year0"]:
        out_towns["Tengah"]["year0_expected"] = 2028

    # ---- 1. BTO launch prices (midpoint of the range), $psf on typical floor areas, vs the resale market that year
    B = json.load(open(f"{RAW}/bto_price_range.json"))
    bto = defaultdict(dict)
    for r in B["records"]:
        t = r["town"].strip()
        if t not in TOWNS or r["room_type"] not in TYPICAL_SQM:
            continue
        try:
            lo, hi = float(r["min_selling_price"]), float(r["max_selling_price"])
        except ValueError:
            continue
        if lo <= 0 or hi <= 0:
            continue
        fy = int(r["financial_year"]); mid = (lo + hi) / 2; psf = mid / (TYPICAL_SQM[r["room_type"]] * SQFT)
        mk = year_mkt.get(fy + 1) and med([year_mkt[fy], year_mkt.get(fy + 1)]) or year_mkt.get(fy)   # FY runs Apr-Mar
        bto[t].setdefault(r["room_type"], []).append({"fy": fy, "min": lo, "max": hi, "mid": mid, "psf": round(psf), "vs_market": round(psf / mk, 3) if mk else None})

    # ---- 3. BTO -> MOP: 4-room / 5-room flats of lease year L resold in L+5..L+6, vs the BTO price of the launch ~4 years before L
    rpi = {r["quarter"]: float(r["index"]) for r in json.load(open(f"{RAW}/hdb_rpi.json"))["records"] if r.get("index") not in (None, "", "na")}
    rpi_year = defaultdict(list)
    for q, v in rpi.items():
        rpi_year[int(q[:4])].append(v)
    rpi_year = {y: sum(v) / len(v) for y, v in rpi_year.items()}
    cohorts = []
    for t in TOWNS:
        for ft in ("3-room", "4-room", "5-room"):
            for b in bto[t].get(ft, []):
                L = b["fy"] + 4                                  # typical BTO wait: ~4 years from launch to completion
                v = [s["price"] for s in sales if s["town"] == t and s["type"] == ft and s["lease"] in (L, L + 1) and L + 5 <= int(s["month"][:4]) <= L + 6]
                mkt = (rpi_year.get(L + 5) / rpi_year[b["fy"]]) if b["fy"] in rpi_year and rpi_year.get(L + 5) else None
                gain = med(v) / b["mid"] - 1 if len(v) >= 30 else None
                cohorts.append({"town": t, "type": ft, "launch_fy": b["fy"], "bto_mid": round(b["mid"]), "mop_year": L + 5, "resales_at_mop": len(v),
                                "resale_median_at_mop": round(med(v)) if len(v) >= 30 else None,
                                "beyond_market_pct": round(100 * ((1 + gain) / mkt - 1), 1) if gain is not None and mkt else None, "gain_pct": round(100 * gain, 1) if gain is not None else None,
                                "market_change_pct": round(100 * (mkt - 1), 1) if mkt else None,
                                "status": "done" if gain is not None else ("waiting for MOP" if L + 5 >= int(last[:4]) else "too few resales")})
    done4 = [c["gain_pct"] for c in cohorts if c["type"] == "4-room" and c["town"] in ("Punggol", "Sengkang") and c["gain_pct"] is not None]
    beyond4 = [c["beyond_market_pct"] for c in cohorts if c["type"] == "4-room" and c["town"] in ("Punggol", "Sengkang") and c["beyond_market_pct"] is not None]
    proj = None
    if done4:
        g = sorted(done4); lo_g, mid_g, hi_g = g[len(g) // 4], stx.median(g), g[(3 * len(g)) // 4]
        tb = sorted(bto["Tengah"].get("4-room", []), key=lambda b: b["fy"])
        proj = {"beyond_market_mid_pct": round(stx.median(beyond4), 1) if beyond4 else None, "beyond_market_range_pct": [min(beyond4), max(beyond4)] if beyond4 else None,
                "basis": f"{len(g)} Punggol/Sengkang 4-room launch years with resales at MOP", "gain_low_pct": lo_g, "gain_mid_pct": mid_g, "gain_high_pct": hi_g,
                "tengah": [{"launch_fy": b["fy"], "bto_mid": round(b["mid"]), "mop_year": f"{b['fy'] + 9}–{b['fy'] + 10}",
                            "if_like_punggol_sengkang": [round(b["mid"] * (1 + lo_g / 100)), round(b["mid"] * (1 + mid_g / 100)), round(b["mid"] * (1 + hi_g / 100))]} for b in tb]}

    # ---- 4. Tengah's neighbours: Bukit Batok / Choa Chu Kang blocks within 1.2 km of Tengah's planned stations vs the rest of their towns
    neighbours = None
    try:
        st = json.load(open("docs/data/stations.json"))
        tpts = [(s["lat"], s["lon"]) for s in st["stations"] if s["name"].startswith("Tengah")]
        coords = json.load(open("data/raw/hdb_block_coords.json"))
        rows = []
        for r in csv.DictReader(open(f"{RAW}/hdb_resale.csv", encoding="utf-8")):
            if r["town"] not in ("BUKIT BATOK", "CHOA CHU KANG") or r["month"] >= this_month:
                continue
            c = coords.get(f"{r['block']}|{r['street_name']}")
            if not c:
                continue
            near = min(km((c[0], c[1]), p) for p in tpts) <= 1.2
            rows.append((r["month"], r["town"].title(), near, float(r["resale_price"]) / (float(r["floor_area_sqm"]) * SQFT), int(r["lease_commence_date"]) // 5))
        tm = defaultdict(list)                      # compare with same-town flats of the same age (lease start in the same 5-year group), same month
        for m, t, n, p, g in rows:
            tm[(t, m, g)].append(p)
        tm = {k: med(v) for k, v in tm.items() if len(v) >= 3}
        def ratio(lo, hi, near):
            v = [p / tm[(t, m, g)] for m, t, n, p, g in rows if lo <= m <= hi and n == near and (t, m, g) in tm]
            return med(v), len(v)
        m12 = (dt.date(int(last[:4]), int(last[5:]), 1) - dt.timedelta(days=330)).strftime("%Y-%m")
        nb, nnb = ratio("2021-08", "2023-07", True); na, nna = ratio(m12, last, True)
        fb, nfb = ratio("2021-08", "2023-07", False); fa, nfa = ratio(m12, last, False)
        if None not in (nb, na, fb, fa):
            neighbours = {"before": "Aug 2021 – Jul 2023", "after": f"{m12} – {last}", "near_change_pts": round(100 * (na - nb), 1),
                          "near_vs_town_before_pct": round(100 * (nb - 1), 1), "near_vs_town_now_pct": round(100 * (na - 1), 1),
                          "sales": [nnb, nna, nfb, nfa], "radius_km": 1.2, "controls": "same town, same month, flats of the same age (lease start within the same 5 years)"}
    except Exception as e:
        print(f"newtowns neighbours: {e}")

    out = {"generated": dt.datetime.now(SGT).isoformat(timespec="minutes"), "latest_month": last, "towns": out_towns, "bto": bto, "cohorts": cohorts,
           "projection": proj, "neighbours": neighbours, "market_year_psf": {y: round(v) for y, v in sorted(year_mkt.items())},
           "sources": {"bto": B["source"], "rpi": "https://data.gov.sg/datasets/d_14f63e595975691e7c24a27ae4c07c79/view",
                       "resale": "https://data.gov.sg/collections/189/view"},
           "assumptions": {"typical_sqm": TYPICAL_SQM, "launch_to_completion_years": 4, "mop_years": 5,
                           "note": "BTO $psf uses typical floor areas; BTO ranges are by financial year (Apr-Mar) and cover FY2008-FY2024, so Punggol's and Sengkang's earliest launches (late 1990s-2007) are not included."}}
    json.dump(out, open("docs/data/newtowns.json", "w"), separators=(",", ":"))
    print(f"newtowns.json: year0 {[(t, v['year0']) for t, v in out_towns.items()]}; {sum(1 for c in cohorts if c['status'] == 'done')} cohorts at MOP")


if __name__ == "__main__":
    main()
