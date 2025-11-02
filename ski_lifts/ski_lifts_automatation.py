# -*- coding: utf-8 -*-
"""
Add POI regions for Catedral Alta Patagonia from Google Sheets into Django Admin.
Reads columns:
A Name_en | B Name_ru | C Genitive_ru | D Locative_ru | F lat | G lon
from spreadsheet: POIs / sheet: Catedral Alta Patagonia
Fills Django Admin form:
Parent (select2) = 677, Type = Point of Interest, Show in suggest = OFF,
Manual center (lat/lon), Translations (en/ru + genitive/locative_ru), is_auto_inflected = No.
Browser window is kept OPEN after the script finishes (Chrome option detach=True).
"""

import time
import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from pathlib import Path
import os

# ---- Paths & env ----
BASE_DIR = Path(__file__).resolve().parents[1]  # .../automatization-scripts

# ------------ CONFIG ------------
# Allow overriding via environment variables; fall back to sensible defaults
CHROME_BINARY  = os.getenv("CHROME_BINARY", r"C:\chrome-win64\chrome.exe")
USER_DATA_DIR  = os.getenv("USER_DATA_DIR", r"C:/Users/nikit/AppData/Local/Google/Chrome for Testing/User Data")
CHROMEDRIVER   = os.getenv("CHROMEDRIVER",  r"C:\chromedriver-win64\chromedriver.exe")

SERVICE_ACCOUNT_FILE = os.getenv(
    "SERVICE_ACCOUNT_FILE",
    str(BASE_DIR / "GCP JSON" / "geo-content-automatization-7039378bbe60.json")
)

ADMIN_URL_ADD  = os.getenv("ADMIN_URL_ADD", "https://content.ostrovok.in/admin/geo/region/add/")
PARENT_SEARCH_TEXT  = os.getenv("PARENT_SEARCH_TEXT", "677")
PARENT_VISIBLE_TEXT = os.getenv("PARENT_VISIBLE_TEXT", "677, Bariloche, AR (City)")
TYPE_VISIBLE_TEXT   = os.getenv("TYPE_VISIBLE_TEXT", "Point of Interest")

# Safe toggles
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
HEADLESS = os.getenv("HEADLESS", "0") == "1"

SPREADSHEET_NAME = "POIs"
WORKSHEET_NAME   = "Catedral Alta Patagonia"

PAUSE_BETWEEN_ITEMS = 0.5
# --------------------------------


# ----- Google Sheets -----
def open_sheet():
    """Authorize and return the target worksheet."""
    if not Path(SERVICE_ACCOUNT_FILE).exists():
        raise FileNotFoundError(
            f"SERVICE_ACCOUNT_FILE not found: {SERVICE_ACCOUNT_FILE}\n"
            "Place your service account JSON locally (excluded by .gitignore) "
            "or set the SERVICE_ACCOUNT_FILE environment variable."
        )

    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open(SPREADSHEET_NAME).worksheet(WORKSHEET_NAME)


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
        lat     = r.get("lat")
        lon     = r.get("lon") or r.get("lon ")
        if not name_en or not name_ru or lat in ("", None) or lon in ("", None):
            continue
        try:
            lat = float(str(lat).replace(",", "."))
            lon = float(str(lon).replace(",", "."))
        except ValueError:
            continue
        rows.append((name_en, name_ru, gen_ru, loc_ru, lat, lon))
    return rows


# ----- Selenium helpers -----
def chrome_driver():
    opts = webdriver.ChromeOptions()
    opts.binary_location = CHROME_BINARY
    opts.add_argument(f"--user-data-dir={USER_DATA_DIR}")
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-software-rasterizer")
    # keep Chrome open after Python exits
    opts.add_experimental_option("detach", True)

    for label, p in [("CHROME_BINARY", CHROME_BINARY), ("CHROMEDRIVER", CHROMEDRIVER)]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{label} not found: {p}")

    service = Service(CHROMEDRIVER)
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(60)
    return driver


def w8(driver, css, cond=EC.presence_of_element_located, to=15):
    """Wait for element located by CSS with a given expected condition."""
    return WebDriverWait(driver, to).until(cond((By.CSS_SELECTOR, css)))


def click_select2_parent(driver):
    """
    Open select2, type the numeric ID, choose the exact entry by visible text,
    or fall back to the first option starting with the parent ID prefix.
    """
    w8(driver, "span.select2-selection.select2-selection--single",
       EC.element_to_be_clickable).click()

    box = w8(driver, "input.select2-search__field")
    box.clear()
    box.send_keys(PARENT_SEARCH_TEXT)
    try:
        target = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
            By.XPATH,
            "//ul[contains(@class,'select2-results__options')]"
            "/li[contains(@class,'select2-results__option') and normalize-space()="
            f"'{PARENT_VISIBLE_TEXT}']"
        )))
    except TimeoutException:
        prefix = PARENT_SEARCH_TEXT.replace("'", r"\'")
        target = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
            By.XPATH,
            "//ul[contains(@class,'select2-results__options')]"
            f"/li[contains(@class,'select2-results__option') and starts-with(normalize-space(), '{prefix}, ')]"
        )))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
    target.click()


def set_type(driver):
    """Select 'Point of Interest' in the type dropdown."""
    sel_el = w8(driver, "select#id_type")
    select = Select(sel_el)

    options = [(o.text.strip(), (o.get_attribute("value") or "").strip()) for o in select.options]

    # 1) Prefer value=poi when available
    for text, val in options:
        if val.lower() == "poi":
            select.select_by_value(val)
            return

    # 2) Fallback by visible text (case-insensitive)
    want = ("point of interest", "poi")
    for text, val in options:
        if any(k in text.lower() for k in want):
            select.select_by_visible_text(text)
            return

    # 3) Explicit fallback to configured visible text
    try:
        select.select_by_visible_text(TYPE_VISIBLE_TEXT)
        return
    except Exception:
        pass

    available = ", ".join([t or f"[value={v}]" for t, v in options])
    raise RuntimeError(f"Cannot find POI type. Available: {available}")


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
    if value is None:
        value = ""
    el.send_keys(value)


def set_coords(driver, lat, lon):
    """Fill manual center coordinates."""
    fill_text(driver, "input#id_manual_lat_center", str(lat))
    fill_text(driver, "input#id_manual_lon_center", str(lon))


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


def add_one(driver, name_en, name_ru, gen_ru, loc_ru, lat, lon):
    """Open the add form, fill all fields, and save; keep the tab open."""
    driver.get(ADMIN_URL_ADD)
    click_select2_parent(driver)
    set_type(driver)
    unset_show_in_suggest(driver)
    set_coords(driver, lat, lon)
    set_translations(driver, name_en, name_ru, gen_ru, loc_ru)
    save_and_continue(driver)


# ----- Main loop -----
def main():
    ws = open_sheet()
    items = read_rows(ws)
    print(f"Records to add: {len(items)}")
    if not items:
        return
    if DRY_RUN:
        print(f"[DRY_RUN] Would add {len(items)} records; skipping Selenium.")
        return

    driver = chrome_driver()
    for i, (name_en, name_ru, gen_ru, loc_ru, lat, lon) in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {name_en} — {name_ru} ({lat},{lon})")
        driver.switch_to.new_window('tab')
        add_one(driver, name_en, name_ru, gen_ru, loc_ru, lat, lon)
        time.sleep(PAUSE_BETWEEN_ITEMS)

    print("✅ Done. All tabs remain open; Chrome stays open thanks to detach=True.")


if __name__ == "__main__":
    main()