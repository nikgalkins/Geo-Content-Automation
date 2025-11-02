# -*- coding: utf-8 -*-
import re
import time
import random
import requests
import unicodedata
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========= CONFIG =========
CREDENTIALS_FILE = r"GCP JSON/geo-content-automatization-7039378bbe60.json"
SPREADSHEET_NAME = "POIs"
SHEET_NAME = "Catedral Alta Patagonia"  # target sheet

# Bounding box for Catedral Alta Patagonia / Bariloche (Argentina)
# (min_lat, min_lon, max_lat, max_lon)
BBOX = (-41.220, -71.600, -41.050, -71.300)

OVERPASS_URLS = [
    "https://overpass.kumi.systems/api",
    "https://overpass-api.de/api",
    "https://overpass.openstreetmap.fr/api",
    "https://overpass.osm.ch/api",
]

UA = "ETG-GeoBot/1.0 (contact: n.galkin@emergingtravel.com)"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

# Header titles for columns A..J
HEADERS_ROW = [
    "lift_name_en",   # A
    "ru_name",        # B
    "ru_genitive",    # C
    "ru_locative",    # D
    "lat",            # E
    "lon",            # F
    "osm_name",       # G
    "osm_type",       # H
    "osm_id",         # I
    "aerialway",      # J
]
# ==========================


# ---------- Google Sheets ----------
def get_ws():
    """Authorize and return the target worksheet."""
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

def ensure_headers(ws):
    """
    Ensure header row A1..J1 exists.
    - If the first row is empty, write all headers.
    - If some cells A1..J1 are empty, fill just those cells.
    """
    row = ws.row_values(1)
    row = (row + [""] * 10)[:10]  # pad to 10 cells
    needs_write = False
    out = row[:]
    for i, title in enumerate(HEADERS_ROW):
        if not out[i].strip():
            out[i] = title
            needs_write = True
    if needs_write:
        ws.update("A1:J1", [out])


# ---------- Name normalization ----------
GENERIC = re.compile(
    r"\b(gondola|gondola lift|cable car|aerial cableway|teleferico|teleférico|teleferik|tele|chairlift|chair lift|bahn|lift|ropeway)\b",
    flags=re.I,
)

def strip_accents(s: str) -> str:
    """Remove diacritics to widen matches (e.g., 'Séxtuple' -> 'Sextuple')."""
    if not s:
        return s
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))

def clean_name(s: str) -> str:
    """Trim and normalize spacing/dashes."""
    s = (s or "").strip()
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"\s+", " ", s)
    return s

def name_variants(name: str):
    """Generate multiple search variants for a lift name around Catedral."""
    base = clean_name(name)
    variants = [base]

    # remove generic words
    stripped = GENERIC.sub("", base).strip(" -")
    if stripped and stripped.lower() != base.lower():
        variants.append(stripped)

    # right-hand part after hyphen
    if " - " in base:
        variants.append(base.split(" - ", 1)[1].strip())
    elif "-" in base:
        parts = [p.strip() for p in base.split("-")]
        if len(parts) > 1:
            variants.append(parts[-1])

    # remove parentheses
    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", base).strip()
    if no_paren and no_paren.lower() != base.lower():
        variants.append(no_paren)

    # accent-stripped variants
    for v in list(variants):
        v2 = strip_accents(v)
        if v2.lower() != v.lower():
            variants.append(v2)

    # local context around Catedral/Bariloche
    root = stripped or base
    ctx = [
        "Catedral Alta Patagonia",
        "Cerro Catedral",
        "Villa Catedral",
        "San Carlos de Bariloche",
        "Bariloche",
        "Río Negro",
        "Patagonia",
        "Argentina",
    ]
    variants += [f"{root} {c}" for c in ctx]

    # unique & non-empty
    out, seen = [], set()
    for v in variants:
        key = v.lower().strip()
        if key and key not in seen:
            out.append(v.strip())
            seen.add(key)
    return out


# ---------- Overpass ----------
def normalize_overpass_url(url: str) -> str:
    url = url.rstrip("/")
    if not url.endswith("interpreter"):
        url += "/interpreter"
    return url

def overpass_query(name_pat: str) -> str:
    """Build Overpass QL query inside bbox for aerialway features and stations."""
    minlat, minlon, maxlat, maxlon = BBOX
    safe = re.escape(name_pat)
    return f"""
[out:json][timeout:45];
(
  node["aerialway"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  way["aerialway"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  relation["aerialway"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  node["aerialway"="station"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  way["aerialway"="station"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  relation["aerialway"="station"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
);
out center tags qt;
"""

def try_overpass(name_pat: str, max_retries: int = 3):
    """Query Overpass; rotate mirrors with backoff and return best match."""
    q = overpass_query(name_pat)
    mirrors = random.sample(OVERPASS_URLS, len(OVERPASS_URLS))
    backoff = 1.0

    for base in mirrors:
        url = normalize_overpass_url(base)
        for _ in range(max_retries):
            try:
                r = requests.post(url, data=q.encode("utf-8"), headers=HEADERS, timeout=60)
                ct = (r.headers.get("Content-Type") or "").lower()
                if r.status_code == 200 and "application/json" in ct:
                    els = (r.json() or {}).get("elements", [])
                    if els:
                        # prefer exact case-insensitive name match if possible
                        def score(el):
                            tags = el.get("tags", {}) or {}
                            nm = tags.get("name", "")
                            return 2 if nm.lower() == name_pat.lower() else 1
                        els.sort(key=score, reverse=True)
                        el = els[0]
                        tags = el.get("tags", {}) or {}
                        lat = el.get("lat") or el.get("center", {}).get("lat")
                        lon = el.get("lon") or el.get("center", {}).get("lon")
                        name_osm = tags.get("name", "")
                        aerialway = tags.get("aerialway", "")
                        osm_type = el.get("type")
                        osm_id = el.get("id")
                        if lat and lon:
                            return float(lat), float(lon), name_osm, osm_type, osm_id, aerialway
                    break
                else:
                    snippet = (r.text or "")[:80].replace("\n", " ")
                    print(f"   overpass non-json {r.status_code} @ {base}: {snippet}")
            except Exception as e:
                print("   overpass error:", e)

            time.sleep(backoff + random.uniform(0, 0.5))
            backoff = min(backoff * 2, 10)
    return None, None, None, None, None, None


# ---------- Nominatim fallback ----------
def try_nominatim(q: str):
    """Fallback to Nominatim (best-effort coordinates and display name)."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "format": "jsonv2",
        "q": q,
        "limit": 1,
        "bounded": 1,
        "viewbox": f"{BBOX[1]},{BBOX[0]},{BBOX[3]},{BBOX[2]}",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if r.status_code == 200 and r.json():
            j = r.json()[0]
            return float(j["lat"]), float(j["lon"]), j.get("display_name", ""), "nominatim", "", ""
    except Exception as e:
        print("   nominatim error:", e)
    return None, None, None, None, None, None


# ---------- Main ----------
def main():
    ws = get_ws()
    ensure_headers(ws)

    rows = ws.get_all_values()
    print(f"Records to process: {len(rows) - 1}")

    for idx, row in enumerate(rows[1:], start=2):
        # Read English name from column A
        if len(row) < 1:
            continue
        name_en = clean_name(row[0])
        if not name_en:
            continue

        print(f"[{idx}] OSM search: {name_en}")
        lat = lon = name_osm = osm_type = osm_id = aerialway = None

        # Overpass with several variants
        for pat in name_variants(name_en):
            lat, lon, name_osm, osm_type, osm_id, aerialway = try_overpass(pat)
            if lat and lon:
                break

        # Fallback to Nominatim with local context
        if not lat:
            for q in (
                f"{name_en} Catedral Alta Patagonia",
                f"{name_en} Cerro Catedral",
                f"{name_en} Bariloche",
            ):
                lat, lon, name_osm, osm_type, osm_id, aerialway = try_nominatim(q)
                if lat and lon:
                    break

        # Write to columns E..J
        if lat and lon:
            ws.update(
                range_name=f"E{idx}:J{idx}",
                values=[[f"{lat:.6f}", f"{lon:.6f}", name_osm or "", osm_type or "", str(osm_id) or "", aerialway or ""]],
            )
            print(f"   ✅ {osm_type}:{osm_id} | {aerialway} | {lat:.6f}, {lon:.6f}")
        else:
            ws.update(range_name=f"E{idx}:J{idx}", values=[["", "", "", "", "", ""]])
            print("   ❌ not found")

        # Be polite to public APIs
        time.sleep(1.0 + random.uniform(0, 0.4))

    print("✅ Done.")


if __name__ == "__main__":
    main()
