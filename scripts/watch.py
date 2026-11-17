"""Watch list -> docs/data/watch.json (rendered by docs/watch.html)

1. Rising stations: weekday ridership growing faster than the network AND nearby HDB resale prices rising faster than
   the rest of the town. Ridership: first vs latest month in pv_history (per weekday, so month length and public holidays
   don't distort it). Prices: blocks within 600 m whose last-12-month $psf vs the 12 months before beat their town.
2. How good is LTA's platform crowd forecast? Every live crowd snapshot (data/raw/lta/crowd_live) is matched with that
   day's forecast (data/raw/lta/crowd_forecast_YYYYMMDD.json) for the same station and half-hour.
"""
import glob, json, os, re, statistics as stx, datetime as dt
from collections import defaultdict, Counter

SGT = dt.timezone(dt.timedelta(hours=8))
RAW = "data/raw/lta"
LEVEL = {"l": "Not crowded", "m": "Moderate", "h": "Crowded"}


def rising():
    h = json.load(open("docs/data/pv_history.json"))
    st = json.load(open("docs/data/stations.json"))
    S = {s["id"]: s for s in st["stations"]}
    code2 = {c["code"]: s for s in st["stations"] for c in s["codes"]}
    m0, m1 = h["months"][0], h["months"][-1]
    if m0 == m1:
        return None

    def per_day(m):
        out, n = defaultdict(float), h["days"][m]["weekday"]
        for key, (tin, tout) in h["data"][m]["weekday"].items():
            s = next((code2[c] for c in re.split(r"[-/ ]+", key) if c in code2), None)
            if s:
                out[s["name"]] += (sum(tin) + sum(tout)) / n
        return out
    a, b = per_day(m0), per_day(m1)
    net = (sum(b.values()) - sum(a.values())) / sum(a.values()) * 100

    p = json.load(open("docs/data/prices.json"))["hdb"]
    town_tr, near = defaultdict(list), defaultdict(list)
    for blk in p["blocks"]:
        lat, lon, psf, n, trend, town, name, price, dist, stn = blk
        if trend is None:
            continue
        town_tr[town].append(trend)
        if stn and dist <= 600:
            near[stn].append((trend, town))
    town_med = {t: stx.median(v) for t, v in town_tr.items()}
    rows = []
    for name in set(a) & set(b):
        if a[name] < 3000:
            continue
        rg = (b[name] - a[name]) / a[name] * 100 - net
        pr = near.get(name, [])
        if len(pr) < 5:
            pg = None
        else:
            pg = stx.median(t - town_med[town] for t, town in pr)
        rows.append({"station": name, "riders_then": round(a[name]), "riders_now": round(b[name]), "riders_vs_network_pct": round(rg, 1),
                     "price_vs_town_pts": None if pg is None else round(pg, 1), "price_trend_pct": None if not pr else round(stx.median(t for t, _ in pr), 1),
                     "blocks": len(pr), "both": pg is not None and rg > 2 and pg > 1})
    rows.sort(key=lambda r: -(r["riders_vs_network_pct"] + (r["price_vs_town_pts"] or 0)))
    return {"from": m0, "to": m1, "network_change_pct": round(net, 1), "both": [r for r in rows if r["both"]],
            "riders_up": sorted(rows, key=lambda r: -r["riders_vs_network_pct"])[:12],
            "riders_down": sorted(rows, key=lambda r: r["riders_vs_network_pct"])[:12],
            "prices_up": sorted([r for r in rows if r["price_vs_town_pts"] is not None], key=lambda r: -r["price_vs_town_pts"])[:12],
            "all": rows}


def forecast_accuracy():
    snaps = sorted(glob.glob(f"{RAW}/crowd_live/*.json"))
    fc_cache, pairs, by_station, by_hour = {}, [], defaultdict(Counter), defaultdict(Counter)
    for f in snaps:
        live = json.load(open(f))
        t = dt.datetime.fromisoformat(live["fetched_at"]).astimezone(SGT)
        day = f"{t:%Y%m%d}"
        if day not in fc_cache:
            fp = f"{RAW}/crowd_forecast_{day}.json"
            fc_cache[day] = json.load(open(fp)).get("stations", {}) if os.path.exists(fp) else {}
        fc = fc_cache[day]
        slot = f"{t.hour:02d}:{'30' if t.minute >= 30 else '00'}"
        for code, lv in live["stations"].items():
            iv = dict(fc.get(code, []))
            if lv in LEVEL and iv.get(slot) in LEVEL:
                pairs.append((iv[slot], lv)); by_station[code][(iv[slot], lv)] += 1; by_hour[t.hour][iv[slot] == lv] += 1
    days = sorted({os.path.basename(f)[:8] for f in snaps})
    out = {"snapshots": len(snaps), "days": len(days), "first_day": days[0] if days else None, "pairs": len(pairs),
           "forecast_lines": sorted({re.match(r"[A-Z]+", c).group(0) for d in fc_cache.values() for c in d})}
    if not pairs:
        return out
    c = Counter(pairs)
    out["exact_pct"] = round(100 * sum(v for (f, l), v in c.items() if f == l) / len(pairs), 1)
    out["always_low_pct"] = round(100 * sum(v for (f, l), v in c.items() if l == "l") / len(pairs), 1)   # score of a lazy "never crowded" forecast
    out["matrix"] = {f: {l: c.get((f, l), 0) for l in LEVEL} for f in LEVEL}
    live_h = sum(v for (f, l), v in c.items() if l == "h"); fc_h = sum(v for (f, l), v in c.items() if f == "h")
    out["crowded_caught_pct"] = round(100 * c.get(("h", "h"), 0) / live_h, 1) if live_h else None
    out["crowded_false_alarm_pct"] = round(100 * (fc_h - c.get(("h", "h"), 0)) / fc_h, 1) if fc_h else None
    out["by_hour"] = {h: round(100 * v[True] / (v[True] + v[False]), 1) for h, v in sorted(by_hour.items())}
    st = json.load(open("docs/data/stations.json"))
    name = {c["code"]: s["name"] for s in st["stations"] for c in s["codes"]}
    worst = []
    for code, cc in by_station.items():
        n = sum(cc.values()); miss = sum(v for (f, l), v in cc.items() if f != l)
        under = sum(v for (f, l), v in cc.items() if "lmh".index(l) > "lmh".index(f))
        worst.append({"station": name.get(code, code), "code": code, "checks": n, "miss_pct": round(100 * miss / n, 1), "busier_than_forecast": under})
    out["worst"] = sorted([w for w in worst if w["miss_pct"] > 0], key=lambda w: (-w["miss_pct"], -w["checks"]))[:15]
    out["min_days_for_verdict"] = 10
    return out


def main():
    out = {"generated": dt.datetime.now(SGT).isoformat(timespec="minutes"), "rising": rising(), "forecast": forecast_accuracy()}
    json.dump(out, open("docs/data/watch.json", "w"), separators=(",", ":"))
    r, f = out["rising"], out["forecast"]
    print(f"watch.json: {len(r['both']) if r else 0} rising stations; forecast checks {f['pairs']} over {f['days']} days, exact {f.get('exact_pct')}%")


if __name__ == "__main__":
    main()
