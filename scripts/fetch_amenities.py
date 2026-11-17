"""Amenities near homes + housing-grant counts (all free data.gov.sg / OneMap, no key)

Writes
  data/raw/amenities/<name>.raw          original download (refreshed every 30 days)
  data/raw/amenities/points.json         {category: [[lat, lon, name], ...]}
  data/raw/amenities/school_coords.json  postal code -> [lat, lon]  (OneMap lookup, kept forever)
  data/raw/grants/cpf_housing_grants.json  HDB "CPF Housing Grants Awarded by Type" (households per financial year)
"""
import csv, html, io, json, os, re, sys, time
import requests

RAW = "data/raw/amenities"
DATASETS = {   # data.gov.sg dataset ids
    "schools": "d_688b934f82c1059ed0a6993d2a829089",       # MOE General information of schools (address + postal code)
    "hawker": "d_4a086da0a5553be1d89383cd90d07ecd",        # NEA Hawker Centres (GEOJSON)
    "parks": "d_0542d48f0991541706b58059381a6eca",         # NParks Parks (points)
    "clinics": "d_548c33ea2d99e29ec63a7cc9edcccedc",       # MOH CHAS clinics (GP clinics)
}
GRANTS = "d_a223380e5856cf06f7eac223e5902230"              # HDB CPF Housing Grants Awarded by Type


def fresh(path, days):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < days * 86400


def poll_download(did):
    for _ in range(12):
        j = requests.get(f"https://api-open.data.gov.sg/v1/public/api/datasets/{did}/poll-download", timeout=60).json()
        if j.get("code") == 0 and j.get("data", {}).get("url"):
            return requests.get(j["data"]["url"], timeout=300).text
        time.sleep(10)
    raise RuntimeError(f"{did}: {j}")


def field(props, *names):
    for n in names:
        for k, v in props.items():
            if k.upper() == n and v not in (None, ""):
                return str(v)
    d = props.get("Description") or props.get("description") or ""
    for n in names:
        m = re.search(rf"<th>{n}</th>\s*<td>(.*?)</td>", d, re.I)
        if m and m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return ""


def points(text):
    """[lat, lon, name] from GeoJSON (points or polygons -> centre) or KML."""
    out = []
    t = text.lstrip()
    if t.startswith("{"):
        for f in json.loads(t).get("features", []):
            g = f.get("geometry") or {}; p = f.get("properties") or {}
            c = g.get("coordinates")
            if not c:
                continue
            flat = []
            def walk(x):
                if isinstance(x[0], (int, float)):
                    flat.append(x)
                else:
                    for y in x: walk(y)
            walk(c)
            lon = sum(q[0] for q in flat) / len(flat); lat = sum(q[1] for q in flat) / len(flat)
            if 1.1 < lat < 1.5 and 103.5 < lon < 104.2:
                out.append([round(lat, 6), round(lon, 6), field(p, "NAME", "LIC_NAME", "HCI_NAME", "BUSINESS_NAME", "Name")])
    else:
        for pm in re.findall(r"<Placemark.*?</Placemark>", text, re.S):
            m = re.search(r"<coordinates>\s*([-\d.]+),([-\d.]+)", pm)
            if not m:
                continue
            nm = re.search(r"<th>(?:NAME|LIC_NAME|BUSINESS_NAME)</th>\s*<td>(.*?)</td>", pm) or re.search(r"<name>(.*?)</name>", pm)
            out.append([round(float(m.group(2)), 6), round(float(m.group(1)), 6), html.unescape(nm.group(1)) if nm else ""])
    return out


def onemap(postal):
    r = requests.get("https://www.onemap.gov.sg/api/common/elastic/search",
                     params={"searchVal": postal, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1}, timeout=30).json()
    for x in r.get("results", []):
        if x.get("POSTAL") == postal:
            return [round(float(x["LATITUDE"]), 6), round(float(x["LONGITUDE"]), 6)]
    return None


def main():
    os.makedirs(RAW, exist_ok=True)
    for name, did in DATASETS.items():
        p = f"{RAW}/{name}.raw"
        if fresh(p, 30):
            continue
        try:
            text = poll_download(did)
            if len(text) > 500:
                open(p, "w", encoding="utf-8").write(text)
                print(f"{name}: downloaded {len(text):,} bytes")
        except Exception as e:
            print(f"{name}: {e}")

    pts = {}
    for name in DATASETS:
        p = f"{RAW}/{name}.raw"
        if name == "schools" or not os.path.exists(p):
            continue
        pts[name] = points(open(p, encoding="utf-8").read())
        print(f"{name}: {len(pts[name])} places")

    # schools: primary (and mixed primary+secondary) located by postal code via OneMap, cached
    sp, cp = f"{RAW}/schools.raw", f"{RAW}/school_coords.json"
    cache = json.load(open(cp)) if os.path.exists(cp) else {}
    if os.path.exists(sp):
        rows = list(csv.DictReader(io.StringIO(open(sp, encoding="utf-8").read())))
        prim, sec = [], []
        t0 = time.time()
        for r in rows:
            postal = (r.get("postal_code") or "").strip().zfill(6)
            if postal not in cache and time.time() - t0 < 1500:
                try:
                    cache[postal] = onemap(postal); time.sleep(0.3)
                except Exception as e:
                    print(f"OneMap {postal}: {e}")
            c = cache.get(postal)
            if not c:
                continue
            lvl = (r.get("mainlevel_code") or "").upper()
            nm = (r.get("school_name") or "").title()
            if "PRIMARY" in lvl or ("MIXED" in lvl and "PRIMARY" in nm.upper()):
                prim.append(c + [nm])
            if "SECONDARY" in lvl or "MIXED" in lvl:
                sec.append(c + [nm])
        json.dump(cache, open(cp, "w"))
        pts["primary_schools"], pts["secondary_schools"] = prim, sec
        print(f"schools: {len(prim)} primary, {len(sec)} secondary located ({sum(1 for v in cache.values() if not v)} postal codes not found)")
    # polyclinics: no current data.gov.sg list, so ask OneMap for every building named "... Polyclinic" (cached 30 days)
    pp = f"{RAW}/polyclinics.json"
    if not fresh(pp, 30):
        found, page = {}, 1
        try:
            while page <= 20:
                r = requests.get("https://www.onemap.gov.sg/api/common/elastic/search",
                                 params={"searchVal": "polyclinic", "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": page}, timeout=30).json()
                for x in r.get("results", []):
                    b = x.get("BUILDING", "")
                    if "POLYCLINIC" in b.upper() and "CLINIC @" not in b.upper():
                        found[b] = [round(float(x["LATITUDE"]), 6), round(float(x["LONGITUDE"]), 6), b.title()]
                if page >= int(r.get("totalNumPages", 1)):
                    break
                page += 1; time.sleep(0.3)
            print(f"polyclinics: OneMap returned {len(found)} buildings over {page} pages: {sorted(found)[:40]}")
            if len(found) >= 10:
                json.dump(list(found.values()), open(pp, "w"))
        except Exception as e:
            print(f"polyclinics: {e}")
    if os.path.exists(pp):
        pts["polyclinics"] = json.load(open(pp)); print(f"polyclinics: {len(pts['polyclinics'])} places")
    if os.path.exists("data/manual/hospitals.csv"):
        pts["hospitals"] = [[float(r["lat"]), float(r["lon"]), r["name"]] for r in csv.DictReader(open("data/manual/hospitals.csv"))]
    json.dump(pts, open(f"{RAW}/points.json", "w"), separators=(",", ":"))

    # housing grants by type (households per financial year)
    os.makedirs("data/raw/grants", exist_ok=True)
    try:
        recs, off = [], 0
        while True:
            j = requests.get("https://data.gov.sg/api/action/datastore_search", params={"resource_id": GRANTS, "limit": 500, "offset": off}, timeout=60).json()
            got = j.get("result", {}).get("records", [])
            recs += got; off += len(got)
            if len(got) < 500:
                break
        if recs:
            json.dump({"source": f"https://data.gov.sg/datasets/{GRANTS}/view", "records": recs}, open("data/raw/grants/cpf_housing_grants.json", "w"), indent=0)
            print(f"grants: {len(recs)} rows")
    except Exception as e:
        print(f"grants: {e}")


if __name__ == "__main__":
    main()
