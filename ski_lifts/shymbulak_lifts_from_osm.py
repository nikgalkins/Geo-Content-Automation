# -*- coding: utf-8 -*-
"""
Fetch Shymbulak (Almaty, KZ) ski lifts from OSM by names listed in a Google Sheet.

Reads:
  A Name_en | B Name_ru | C Genitive_ru | D Locative_ru
Writes:
  E lat | F lon | G osm_name | H osm_type | I osm_id | J aerialway

Features:
- Overpass API with mirror rotation, retries, backoff.
- Matches by 'name' and 'name:en' (lifts and stations).
- Shymbulak-specific aliases/transliterations (Medeu/Medeo, Shymbulak/Chimbulak, etc.).
- Nominatim fallback if Overpass returns nothing.
- Polite throttling for public APIs.

Usage:
  python ski_lifts/shymbulak_lifts_from_osm.py
"""

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
SHEET_NAME = "Shymbulak"  # target sheet

# Bounding box for Medeu ↔ Shymbulak area (Almaty, Kazakhstan)
# (min_lat, min_lon, max_lat, max_lon) — broad enough to include Medeu station and upper lifts
BBOX = (43.10, 76.85, 43.25, 77.15)

OVERPASS_URLS = [
    "https://overpass.kumi.systems/api",
    "https://overpass-api.de/api",
    "https://overpass.openstreetmap.fr/api",
    "https://overpass.osm.ch/api",
]

UA = "ETG-GeoBot/1.0 (contact: geo-team@example.com)"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

HEADERS_ROW = [
    "Name_en",       # A
    "Name_ru",       # B
    "Genitive_ru",   # C
    "Locative_ru",   # D
    "lat",           # E
    "lon",           # F
    "osm_name",      # G
    "osm_type",      # H
    "osm_id",        # I
    "aerialway",     # J
]

# Shymbulak-specific alias expansions to improve matching
SHMB_SPECIALS = {
    "Medeu": ["Medeo", "Medeu Station", "Medeo Station"],
    "Shymbulak": ["Chimbulak", "Shymbulak Resort", "Chimbulak Resort"],
    "Shymbulak Cableway": [
        "Medeu–Shymbulak Gondola", "Medeu-Shymbulak Gondola",
        "Medeo–Shymbulak Gondola", "Medeo-Shymbulak Gondola",
        "Medeu–Shymbulak Cable Car", "Medeo–Shymbulak Cable Car",
        "Gondola Medeu Shymbulak", "Cableway Medeu Shymbulak",
    ],
    "Combi-1": ["Combi 1", "Combi I", "Kombi-1", "Kombi 1"],
    "Combi-2": ["Combi 2", "Combi II", "Kombi-2", "Kombi 2"],
    "KKD-4": ["KKD 4", "KKD4"],
    "Konus": ["Konus T-bar", "Konus drag lift", "Cone Lift"],
    "Left Talgar": ["Left Talgar Lift", "Levyi Talgar", "Levyy Talgar"],
}

# ==========================


# ---------- Google Sheets ----------
def get_ws():
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

def ensure_headers(ws):
    """Ensure A1..J1 match expected headers (fill missing cells only)."""
    row = ws.row_values(1)
    row = (row + [""] * len(HEADERS_ROW))[:len(HEADERS_ROW)]
    changed = False
    for i, title in enumerate(HEADERS_ROW):
        if not (row[i] or "").strip():
            row[i] = title
            changed = True
    if changed:
        ws.update(values=[row], range_name="A1:J1")


# ---------- Name normalization ----------
GENERIC = re.compile(
    r"\b(gondola|gondola lift|cable car|aerial cableway|teleferico|teleférico|teleferik|tele|"
    r"chairlift|chair lift|bahn|lift|ropeway|express|pass|line|station)\b",
    flags=re.I,
)

def strip_accents(s: str) -> str:
    if not s:
        return s
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))

def clean_name(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"\s+", " ", s)
    return s

def name_variants(name: str):
    """Generate robust search variants for Shymbulak lifts."""
    base = clean_name(name)
    variants = [base]

    # remove generic words (bahn/lift/etc.)
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

    # Shymbulak-specific aliases
    for k, extra in SHMB_SPECIALS.items():
        if base.lower() == k.lower():
            variants += extra

    # local context boosts (Shymbulak and surroundings)
    root = stripped or base
    ctx = [
        "Shymbulak", "Chimbulak", "Medeu", "Medeo",
        "Ile-Alatau National Park", "Almaty", "Kazakhstan", "Trans-Ili Alatau",
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
    """Search aerialway features and stations by name within bbox."""
    minlat, minlon, maxlat, maxlon = BBOX
    safe = re.escape(name_pat)
    return f"""
[out:json][timeout:45];
(
  node["aerialway"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  way["aerialway"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  relation["aerialway"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});

  node["aerialway"]["name:en"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  way["aerialway"]["name:en"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  relation["aerialway"]["name:en"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});

  node["aerialway"="station"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  way["aerialway"="station"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  relation["aerialway"="station"]["name"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});

  node["aerialway"="station"]["name:en"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  way["aerialway"="station"]["name:en"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
  relation["aerialway"="station"]["name:en"~"{safe}",i]({minlat},{minlon},{maxlat},{maxlon});
);
out center tags qt;
"""

def _score_overpass_element(el, needle_lower: str) -> int:
    """Score elements: prefer exact name match and real aerialway features."""
    tags = el.get("tags") or {}
    name = (tags.get("name") or tags.get("name:en") or "").lower()
    is_station = (tags.get("aerialway") == "station") or (el.get("tags", {}).get("public_transport") == "station")
    is_aerial = "aerialway" in tags and tags.get("aerialway") != "station"

    score = 0
    if name == needle_lower:
        score += 5
    if is_aerial:
        score += 3
    if is_station:
        score += 1
    # Prefer ways/relations (geometry over single nodes) if tie
    typ = el.get("type")
    if typ == "relation":
        score += 2
    elif typ == "way":
        score += 1
    return score

def try_overpass(name_pat: str, max_retries: int = 3):
    """Query Overpass, rotate mirrors, backoff; return best match with details."""
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
                        needle = name_pat.lower()
                        els.sort(key=lambda e: _score_overpass_element(e, needle), reverse=True)
                        el = els[0]
                        tags = el.get("tags", {}) or {}
                        lat = el.get("lat") or el.get("center", {}).get("lat")
                        lon = el.get("lon") or el.get("center", {}).get("lon")
                        if lat and lon:
                            name_osm = tags.get("name") or tags.get("name:en") or ""
                            aerialway = tags.get("aerialway", "")
                            osm_type = el.get("type")            # node/way/relation
                            osm_id = el.get("id")
                            return (float(lat), float(lon), name_osm,
                                    osm_type or "", str(osm_id) or "", aerialway or "")
                    break
            except Exception:
                pass
            time.sleep(backoff + random.uniform(0, 0.5))
            backoff = min(backoff * 2, 10)
    return None, None, None, None, None, None


# ---------- Nominatim fallback ----------
def try_nominatim(q: str):
    """Best-effort coordinates and display name if Overpass fails."""
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
            return (float(j.get("lat")), float(j.get("lon")),
                    j.get("display_name", ""), "nominatim", "", "")
    except Exception:
        pass
    return None, None, None, None, None, None


# ---------- Main ----------
def main():
    ws = get_ws()
    ensure_headers(ws)

    rows = ws.get_all_values()
    print(f"Records to process: {len(rows) - 1}")

    for idx, row in enumerate(rows[1:], start=2):
        # Read English name from column A (index 0)
        if len(row) < 1:
            continue
        name_en = clean_name(row[0])
        if not name_en:
            continue

        print(f"[{idx}] OSM search: {name_en}")
        lat = lon = name_osm = osm_type = osm_id = aerialway = None

        # Overpass: try multiple variants
        for pat in name_variants(name_en):
            (lat, lon, name_osm, osm_type, osm_id, aerialway) = try_overpass(pat)
            if lat and lon:
                break

        # Nominatim fallback with local context
        if not lat:
            for q in (
                f"{name_en} Shymbulak",
                f"{name_en} Chimbulak",
                f"{name_en} Medeu",
                f"{name_en} Almaty",
                f"{name_en} Ile-Alatau National Park",
                f"{name_en} Kazakhstan",
            ):
                (lat, lon, name_osm, osm_type, osm_id, aerialway) = try_nominatim(q)
                if lat and lon:
                    break

        # Write to columns E..J
        if lat and lon:
            ws.update(
                values=[[f"{lat:.6f}", f"{lon:.6f}", name_osm or "",
                         osm_type or "", str(osm_id) or "", aerialway or ""]],
                range_name=f"E{idx}:J{idx}",
            )
            print(f"   ✅ {osm_type}:{osm_id} | {aerialway} | {lat:.6f}, {lon:.6f}")
        else:
            ws.update(values=[["", "", "", "", "", ""]], range_name=f"E{idx}:J{idx}")
            print("   ❌ not found")

        # Be polite to public APIs
        time.sleep(1.0 + random.uniform(0, 0.4))

    print("✅ Done.")


if __name__ == "__main__":
    main()
