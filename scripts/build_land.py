"""Rasterise Singapore's land outline into the pixel grid used by docs/sim.html -> docs/data/sg_land.json.

Source: URA Master Plan 2014 country boundary, via github.com/yinshanyang/singapore (maps/0-country.geojson).
Usage: python scripts/build_land.py path/to/0-country.geojson
"""
import json, sys

W, H, LON0, LON1, LAT0, LAT1 = 420, 232, 103.6, 104.02, 1.475, 1.24


def rings(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        for ring in poly:
            yield [((lon - LON0) / (LON1 - LON0) * W, (lat - LAT0) / (LAT1 - LAT0) * H) for lon, lat in ring]


def main(path):
    gj = json.load(open(path))
    feats = gj["features"] if "features" in gj else [gj]
    edges = []
    for f in feats:
        for r in rings(f["geometry"]):
            edges += [(r[i], r[(i + 1) % len(r)]) for i in range(len(r))]
    rows = []
    for y in range(H):
        cy = y + 0.5
        xs = sorted(x0 + (cy - y0) * (x1 - x0) / (y1 - y0) for (x0, y0), (x1, y1) in edges if (y0 <= cy) != (y1 <= cy))
        runs = []  # [start, length] pairs of land pixels (even-odd rule)
        for a, b in zip(xs[0::2], xs[1::2]):
            s, e = max(0, round(a)), min(W, round(b))
            if e > s:
                runs += [s, e - s]
        rows.append(runs)
    json.dump({"w": W, "h": H, "bounds": [LON0, LAT0, LON1, LAT1], "rows": rows,
               "source": "URA Master Plan 2014 boundary via github.com/yinshanyang/singapore"}, open("docs/data/sg_land.json", "w"), separators=(",", ":"))
    print("land pixels:", sum(sum(r[1::2]) for r in rows))


if __name__ == "__main__":
    main(sys.argv[1])
