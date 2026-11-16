"""Build docs/data/stations.json: every open MRT/LRT station plus every announced future station.

Inputs
  --rail     sg-rail.geojson from github.com/cheeaun/sgraildata (LTA station points, open network)
  --mp19     URA Master Plan 2019 rail station layer (data.gov.sg d_8d886e3a83934d7447acdf5bc6959999)
  --planned  data/manual/planned_stations.csv (curated from LTA news releases, see `source` column)

Usage: python scripts/build_stations.py --rail X --mp19 Y [--planned data/manual/planned_stations.csv]
"""
import argparse, csv, json, re, os
from collections import defaultdict

LINES = {
    "NS": ("North-South Line", "#D42E12", "MRT"), "EW": ("East-West Line", "#009645", "MRT"),
    "CG": ("East-West Line (Changi branch)", "#009645", "MRT"), "NE": ("North East Line", "#9900AA", "MRT"),
    "CC": ("Circle Line", "#FA9E0D", "MRT"), "DT": ("Downtown Line", "#005EC4", "MRT"),
    "TE": ("Thomson-East Coast Line", "#9D5B25", "MRT"),
    "JS": ("Jurong Region Line", "#0099AA", "MRT"), "JW": ("Jurong Region Line (West)", "#0099AA", "MRT"),
    "JE": ("Jurong Region Line (East)", "#0099AA", "MRT"),
    "CR": ("Cross Island Line", "#97C616", "MRT"), "CP": ("Cross Island Line (Punggol Ext.)", "#97C616", "MRT"),
    "BP": ("Bukit Panjang LRT", "#748477", "LRT"), "SE": ("Sengkang LRT (East)", "#748477", "LRT"),
    "SW": ("Sengkang LRT (West)", "#748477", "LRT"), "PE": ("Punggol LRT (East)", "#748477", "LRT"),
    "PW": ("Punggol LRT (West)", "#748477", "LRT"), "STC": ("Sengkang LRT", "#748477", "LRT"),
    "PTC": ("Punggol LRT", "#748477", "LRT"),
}
# Links that are not "next code number" (branches, loops, shared termini)
EXTRA_EDGES = [("CC34", "CC4"), ("EW4", "CG1"), ("JS7", "JW1"), ("JS3", "JE1"), ("CR5", "CP2"),
               ("BP13", "BP6"), ("STC", "SE1"), ("SE5", "STC"), ("STC", "SW1"), ("SW8", "STC"),
               ("PTC", "PE1"), ("PE7", "PTC"), ("PTC", "PW1"), ("PW7", "PTC")]
NO_SEQ = {"CP1"}  # CP1 is the same platform pair as CR5 (Pasir Ris)

# Corridors announced for study only (no station locations published) - drawn dashed, never counted as coverage
CORRIDORS = [
    {"name": "JRL West Coast Extension (Phase 1)", "status": "engineering study from 2026",
     "path": [[1.3207, 103.74494], [1.3005, 103.7655]], "source": "https://www.lta.gov.sg/content/ltagov/en/newsroom/2026/3/news-releases/next-phase-of-rail-development.html"},
    {"name": "JRL West Coast Extension (Phase 2, to Kent Ridge)", "status": "early 2040s",
     "path": [[1.3005, 103.7655], [1.29345, 103.78455]], "source": "https://sgtrains.com/network.html"},
    {"name": "Seletar Line (Sengkang West - Serangoon North corridor)", "status": "engineering study from 2026",
     "path": [[1.3930, 103.8790], [1.3705, 103.8739], [1.3497, 103.8732]], "source": "https://www.lta.gov.sg/content/ltagov/en/newsroom/2026/3/news-releases/next-phase-of-rail-development.html"},
    {"name": "TEL extension to Changi Terminal 5", "status": "mid-2030s",
     "path": [[1.32037, 103.9569], [1.3560, 104.0050]], "source": "https://www.tnp.sg/news/singapore/hume-mrt-station-opening-q2-2025-more-follow"},
    {"name": "Tengah Line", "status": "engineering study from 2026 (alignment not published)", "path": [],
     "source": "https://www.lta.gov.sg/content/ltagov/en/newsroom/2026/3/news-releases/next-phase-of-rail-development.html"},
]


def parse_code(code):
    m = re.match(r"^([A-Z]+)(\d*)([A-Z]?)$", code)
    if not m:
        return None
    return m.group(1), int(m.group(2) or 0), m.group(3)


def norm(n):
    n = (n or "").upper().replace(" INTERCHANGE", "").replace(" STATION", "").replace(" MRT", "").strip()
    return re.sub(r"\s+", " ", n)


def mp19_centroids(path):
    d = json.load(open(path))
    out = {}
    for f in d["features"]:
        p = f["properties"]
        if p.get("RAIL_TYPE") != "MRT":
            continue
        g = f["geometry"]
        ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
        lat = sum(c[1] for c in ring) / len(ring); lon = sum(c[0] for c in ring) / len(ring)
        out.setdefault(norm(p.get("NAME")), []).append((lat, lon))
    return {k: (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v)) for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rail", required=True); ap.add_argument("--mp19", required=True)
    ap.add_argument("--planned", default="data/manual/planned_stations.csv")
    ap.add_argument("--out", default="docs/data/stations.json")
    a = ap.parse_args()

    stations = {}  # key: normalized name -> record
    rail = json.load(open(a.rail))
    for f in rail["features"]:
        p = f["properties"]
        if f["geometry"]["type"] != "Point" or p.get("stop_type") != "station":
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        codes = [c if c != "CG" else "CG0" for c in p["station_codes"].split("-")]
        key = norm(p["name"])
        stations[key] = {"name": p["name"], "lat": round(lat, 6), "lon": round(lon, 6), "coord_source": "LTA (via sgraildata)",
                         "codes": [{"code": c, "status": "open", "opening": "open", "open_year": 0} for c in codes]}

    mp = mp19_centroids(a.mp19)
    for r in csv.DictReader(open(a.planned)):
        key = norm(r["name"])
        code = {"code": r["code"], "status": r["status"], "opening": r["opening"], "open_year": float(r["open_year"]), "source": r["source"]}
        if key in stations:
            if r["code"] not in [c["code"] for c in stations[key]["codes"]]:
                stations[key]["codes"].append(code)
            continue
        if r["lat"]:
            lat, lon, src = float(r["lat"]), float(r["lon"]), r["coord_source"]
        elif key in mp:
            (lat, lon), src = mp[key], "URA Master Plan 2019"
        else:
            raise SystemExit(f"No coordinates for {r['code']} {r['name']}")
        stations[key] = {"name": r["name"], "lat": round(lat, 6), "lon": round(lon, 6), "coord_source": src, "codes": [code]}

    recs = sorted(stations.values(), key=lambda s: s["name"])
    for i, s in enumerate(recs):
        s["id"] = i
        s["open_year"] = min(c["open_year"] for c in s["codes"])
        s["mode"] = "LRT" if all(LINES.get(parse_code(c["code"])[0], ("", "", "MRT"))[2] == "LRT" for c in s["codes"]) else "MRT"

    by_code = {}
    for s in recs:
        for c in s["codes"]:
            by_code[c["code"]] = (s["id"], c["open_year"])
    seq = defaultdict(list)
    for code in by_code:
        if code in NO_SEQ:
            continue
        pc = parse_code(code)
        if pc:
            seq[pc[0]].append((pc[1], pc[2], code))
    # A link between two stations is in service from when both ends are open until a station
    # in between opens (e.g. TE9-TE11 runs non-stop until Mount Pleasant TE10 opens).
    edge_out = []
    for pre, items in seq.items():
        items.sort()
        for i in range(len(items)):
            between_min = 9999.0
            for j in range(i + 1, len(items)):
                c1, c2 = items[i][2], items[j][2]
                (s1, y1), (s2, y2) = by_code[c1], by_code[c2]
                start = max(y1, y2)
                if start < between_min and s1 != s2:
                    edge_out.append({"line": pre, "a": s1, "b": s2, "codes": [c1, c2], "open_year": start,
                                     "until": None if between_min == 9999.0 else between_min})
                between_min = min(between_min, y2)
                if y2 <= y1:
                    break
    for c1, c2 in EXTRA_EDGES:
        if c1 in by_code and c2 in by_code:
            (s1, y1), (s2, y2) = by_code[c1], by_code[c2]
            edge_out.append({"line": parse_code(c1)[0], "a": s1, "b": s2, "codes": [c1, c2], "open_year": max(y1, y2), "until": None})

    out = {"generated_from": ["LTA station points via github.com/cheeaun/sgraildata", "URA Master Plan 2019 rail station layer",
                              "data/manual/planned_stations.csv (LTA news releases)"],
           "lines": {k: {"name": v[0], "color": v[1], "mode": v[2]} for k, v in LINES.items()},
           "stations": recs, "edges": edge_out, "corridors": CORRIDORS}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), separators=(",", ":"))
    n_open = sum(1 for s in recs if s["open_year"] == 0)
    print(f"{len(recs)} stations ({n_open} open, {len(recs)-n_open} future), {len(edge_out)} track links -> {a.out}")


if __name__ == "__main__":
    main()
