# -*- coding: utf-8 -*-
"""
Universal uploader: add POI regions from Google Sheets into Django Admin.

Reads columns by header:
A Name_en | B Name_ru | C Genitive_ru | D Locative_ru | E lat | F lon
(Extra columns are ignored.)

Usage (CLI overrides env vars):
  python ski_lifts/admin_upload_from_sheet.py \
    --spreadsheet "POIs" \
    --worksheet "Gudauri" \
    --admin-url "https://content.ostrovok.in/admin/geo/region/add/" \
    --parent-id "6139982" \
    --parent-visible "6139982, Gudauri, GE (City)" \
    --type-visible "Point of Interest"

Environment variables (each has a sensible default):
  CHROME_BINARY, USER_DATA_DIR, CHROMEDRIVER,
  SERVICE_ACCOUNT_FILE, ADMIN_URL_ADD, PARENT_SEARCH_TEXT, PARENT_VISIBLE_TEXT,
  TYPE_VISIBLE_TEXT, SPREADSHEET_NAME, WORKSHEET_NAME, DRY_RUN (0/1), HEADLESS (0/1)

Behavior:
- Keeps Chrome open after the script finishes (detach=True).
- Selects Parent via select2, Type via <select>.
- Fills Manual center lat/lon and translations (en/ru + genitive/locative).
- Ensures "Show in suggest" is OFF; sets is_auto_inflected=No when available.
"""

import os
import time
import argparse
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ---------- Defaults & ENV ----------
DEFAULTS = {
    "CHROME_BINARY": r"C:\chrome-win64\chrome.exe",
    "USER_DATA_DIR": r"C:/Users/nikit/AppData/Local/Google/Chrome for Testing/User Data",
    "CHROMEDRIVER": r"C:\chromedriver-win64\chromedriver.exe",
    "SERVICE_ACCOUNT_FILE": "GCP JSON/geo-content-automatization-7039378bbe60.json",

    "ADMIN_URL_ADD": "https://content.ostrovok.in/admin/geo/region/add/",
    "PARENT_SEARCH_TEXT": "6139982",
    "PARENT_VISIBLE_TEXT": "6139982, Gudauri, GE (City)",
    "TYPE_VISIBLE_TEXT": "Point of Interest",

    "SPREADSHEET_NAME": "POIs",
    "WORKSHEET_NAME": "Gudauri",

    "DRY_RUN": "0",
    "HEADLESS": "0",
}

def env(key: str) -> str:
    return os.getenv(key, DEFAULTS[key])

# ---------- Google Sheets ----------
def open_sheet(spreadsheet_name: str, worksheet_name: str, service_account_file: str):
    """Authorize and return the target worksheet."""
    saf = Path(service_account_file)
    if not saf.exists():
        raise FileNotFoundError(
            f"SERVICE_ACCOUNT_FILE not found: {saf}\n"
            "Place your service account JSON locally (excluded by .gitignore) "
            "or set SERVICE_ACCOUNT_FILE env var."
        )
    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(str(saf), scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open(spreadsheet_name).worksheet(worksheet_name)

def read_rows(ws):
    """
    Read all records by header. Expected headers:
    Name_en, Name_ru, Genitive_ru, Locative_ru, lat, lon
    Returns a list of tuples: (name_en, name_ru, gen_ru, loc_ru, lat, lon)
    """
    data = ws.get_all_records()
    rows = []
    for r in data:
        name_en = (r.get("Name_en") or "").strip()
        name_ru = (r.get("Name_ru") or "").strip()
        gen_ru  = (r.get("Genitive_ru") or "").strip()
        loc_ru  = (r.get("Locative_ru") or "").strip()
        lat     = r.get("lat") or r.get("Lat") or r.get("LAT")
        lon     = r.get("lon") or r.get("Lon") or r.get("LON")
        if not name_en or not name_ru or lat in ("", None) or lon in ("", None):
            continue
        try:
            lat = float(str(lat).replace(",", "."))
            lon = float(str(lon).replace(",", "."))
        except ValueError:
            continue
        rows.append((name_en, name_ru, gen_ru, loc_ru, lat, lon))
    return rows

# ---------- Selenium helpers ----------
def chrome_driver(chrome_binary: str, user_data_dir: str, chromedriver: str, headless: bool):
    opts = webdriver.ChromeOptions()
    opts.binary_location = chrome_binary
    opts.add_argument(f"--user-data-dir={user_data_dir}")
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-software-rasterizer")
    # keep Chrome open after Python exits
    opts.add_experimental_option("detach", True)

    for label, p in [("CHROME_BINARY", chrome_binary), ("CHROMEDRIVER", chromedriver)]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{label} not found: {p}")

    service = Service(chromedriver)
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(60)
    return driver

def w8(driver, css, cond=EC.presence_of_element_located, to=15):
    """Wait for element located by CSS with a given expected condition."""
    return WebDriverWait(driver, to).until(cond((By.CSS_SELECTOR, css)))

def click_select2_parent(driver, parent_search_text: str, parent_visible_text: str):
    """
    Open select2, type the numeric ID, choose the exact entry by visible text,
    or fall back to the first option starting with the parent ID prefix.
    """
    w8(driver, "span.select2-selection.select2-selection--single",
       EC.element_to_be_clickable).click()
    box = w8(driver, "input.select2-search__field")
    box.clear()
    box.send_keys(parent_search_text)
    try:
        target = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
            By.XPATH,
            "//ul[contains(@class,'select2-results__options')]"
            "/li[contains(@class,'select2-results__option') and normalize-space()="
            f"'{parent_visible_text}']"
        )))
    except TimeoutException:
        prefix = parent_search_text.replace("'", r"\'")
        target = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
            By.XPATH,
            "//ul[contains(@class,'select2-results__options')]"
            f"/li[contains(@class,'select2-results__option') and starts-with(normalize-space(), '{prefix}, ')]"
        )))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
    target.click()

def set_type(driver, type_visible_text: str):
    """Select desired Type in the type dropdown (prefers value 'poi')."""
    sel_el = w8(driver, "select#id_type")
    select = Select(sel_el)
    options = [(o.text.strip(), (o.get_attribute("value") or "").strip()) for o in select.options]

    # 1) Prefer explicit value 'poi' if exists
    for text, val in options:
        if val.lower() == "poi":
            select.select_by_value(val)
            return

    # 2) Fallback by visible text (case-insensitive)
    want = (type_visible_text or "").strip().lower()
    for text, val in options:
        if text.strip().lower() == want:
            select.select_by_visible_text(text)
            return

    # 3) Heuristic fallback for "Point of Interest"
    for text, val in options:
        if "point of interest" in text.lower() or text.lower() in ("poi",):
            select.select_by_visible_text(text)
            return

    available = ", ".join([t or f"[value={v}]" for t, v in options])
    raise RuntimeError(f"Cannot find desired type. Available: {available}")

def unset_show_in_suggest(driver):
    """Ensure 'Show in suggest' checkbox is OFF."""
    cb = w8(driver, "input#id_show_in_suggest")
    if cb.is_selected():
        cb.click()

def fill_text(driver, css, value):
    """Scroll to an input, clear it, and type a new value."""
    el = w8(driver, css, EC.element_to_be_clickable)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
    el.send_keys("" if value is None else str(value))

def set_coords(driver, lat, lon):
    """Fill manual center coordinates."""
    fill_text(driver, "input#id_manual_lat_center", lat)
    fill_text(driver, "input#id_manual_lon_center", lon)

def set_translations(driver, name_en, name_ru, gen_ru, loc_ru):
    """Fill English and Russian translations including genitive/locative; disable auto-inflect."""
    fill_text(driver, "input#id_translations-0-name", name_en)
    fill_text(driver, "input#id_translations-1-name", name_ru)
    fill_text(driver, "input#id_translations-1-genitive", gen_ru)
    fill_text(driver, "input#id_translations-1-locative_in", loc_ru)
    try:
        Select(w8(driver, "select#id_translations-1-is_auto_inflected")).select_by_visible_text("No")
    except Exception:
        pass

def save_and_continue(driver):
    """Click 'Save and continue editing' and wait for a page reload or success message."""
    btn = w8(driver, "input[name='_continue']", EC.element_to_be_clickable)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    old_url = driver.current_url
    btn.click()
    try:
        WebDriverWait(driver, 12).until(
            lambda d: d.current_url != old_url or
                      d.find_elements(By.CSS_SELECTOR, ".messagelist, .success") or
                      d.find_elements(By.TAG_NAME, "form")
        )
    except TimeoutException:
        pass

def add_one(driver, admin_url_add, parent_search_text, parent_visible_text,
            type_visible_text, name_en, name_ru, gen_ru, loc_ru, lat, lon):
    """Open the add form, fill all fields, and save; keep the tab open."""
    driver.get(admin_url_add)
    click_select2_parent(driver, parent_search_text, parent_visible_text)
    set_type(driver, type_visible_text)
    unset_show_in_suggest(driver)
    set_coords(driver, lat, lon)
    set_translations(driver, name_en, name_ru, gen_ru, loc_ru)
    save_and_continue(driver)

# ---------- Main ----------
def parse_args():
    p = argparse.ArgumentParser(description="Upload POIs from Google Sheets to Django Admin.")
    p.add_argument("--spreadsheet", default=env("SPREADSHEET_NAME"))
    p.add_argument("--worksheet", default=env("WORKSHEET_NAME"))
    p.add_argument("--admin-url", default=env("ADMIN_URL_ADD"))
    p.add_argument("--parent-id", default=env("PARENT_SEARCH_TEXT"))
    p.add_argument("--parent-visible", default=env("PARENT_VISIBLE_TEXT"))
    p.add_argument("--type-visible", default=env("TYPE_VISIBLE_TEXT"))
    p.add_argument("--service-account", default=env("SERVICE_ACCOUNT_FILE"))
    p.add_argument("--headless", action="store_true", help="Force headless mode")
    p.add_argument("--dry-run", action="store_true", help="Don't open Selenium, just list items")
    return p.parse_args()

def main():
    args = parse_args()

    # Resolve paths/env
    chrome_binary = env("CHROME_BINARY")
    user_data_dir = env("USER_DATA_DIR")
    chromedriver  = env("CHROMEDRIVER")
    service_account_file = args.service_account

    # DRY_RUN / HEADLESS
    dry_run = args.dry_run or (env("DRY_RUN") == "1")
    headless = args.headless or (env("HEADLESS") == "1")

    ws = open_sheet(args.spreadsheet, args.worksheet, service_account_file)
    items = read_rows(ws)
    print(f"Records to add from '{args.spreadsheet}/{args.worksheet}': {len(items)}")
    if not items or dry_run:
        if dry_run:
            for i, it in enumerate(items, 1):
                name_en, name_ru, _, _, lat, lon = it
                print(f"[DRY] {i:>2} {name_en} — {name_ru} ({lat},{lon})")
            print("[DRY] Done.")
        return

    driver = chrome_driver(chrome_binary, user_data_dir, chromedriver, headless)
    for i, (name_en, name_ru, gen_ru, loc_ru, lat, lon) in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {name_en} — {name_ru} ({lat},{lon})")
        driver.switch_to.new_window('tab')
        add_one(driver, args.admin_url, args.parent_id, args.parent_visible,
                args.type_visible, name_en, name_ru, gen_ru, loc_ru, lat, lon)
        time.sleep(0.5)

    print("✅ Done. All tabs remain open; Chrome stays open thanks to detach=True.")

if __name__ == "__main__":
    main()
