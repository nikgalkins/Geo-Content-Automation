# -*- coding: utf-8 -*-
import time
import logging
from typing import Tuple

# ---- Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---- Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ================== CONFIG ==================
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = r"GCP JSON/geo-content-automatization-7039378bbe60.json"

SPREADSHEET_NAME = "Slugs"
SHEET_NAME = "Canada"  # sheet name

# Chrome for Testing (your local paths)
CHROME_BINARY = r"C:\chrome-win64\chrome.exe"
CHROMEDRIVER = r"C:\chromedriver-win64\chromedriver.exe"
USER_DATA_DIR = r"C:/Users/nikit/AppData/Local/Google/Chrome for Testing/User Data"

BASE_URL = "https://content.ostrovok.in/content/basedata_admin/{slug}/view"

PAGE_TIMEOUT = 20
# ============================================

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def init_driver() -> webdriver.Chrome:
    """Initialize Chrome WebDriver with stable options."""
    service = webdriver.chrome.service.Service(CHROMEDRIVER)
    options = webdriver.ChromeOptions()
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
    options.binary_location = CHROME_BINARY
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_window_size(1400, 900)
    return driver


def connect_sheet():
    """Authorize with Google Sheets and ensure the expected header structure."""
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPE)
    client = gspread.authorize(creds)
    ss = client.open(SPREADSHEET_NAME)
    sheet = ss.worksheet(SHEET_NAME)

    expected = ["slugs", "Region_raw", "Region_id", "Region_name", "Region_country"]
    headers = sheet.row_values(1)
    if headers[:len(expected)] != expected:
        sheet.update("A1:E1", [expected])
    return sheet


def read_slugs(sheet) -> list[str]:
    """Read slugs from column A, skipping empty rows and header."""
    values = sheet.col_values(1)  # Column A
    return [v.strip() for v in values[1:] if v.strip()]


def get_region_from_admin(driver: webdriver.Chrome, slug: str) -> Tuple[str, str]:
    """
    Return (region_raw_text, region_id_value).
    If nothing found — both values are empty strings.
    """
    url = BASE_URL.format(slug=slug)
    driver.get(url)

    try:
        name_el = WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input#id_region_name"))
        )
        id_el = WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input#id_region_id"))
        )

        region_raw = (name_el.get_attribute("value") or "").strip()
        region_id = (id_el.get_attribute("value") or "").strip()
        logging.info(f"{slug}: region_raw='{region_raw}' | region_id={region_id}")
        return region_raw, region_id
    except Exception as e:
        logging.error(f"{slug}: failed to fetch region info: {e}")
        return "", ""


def parse_region(region_raw: str, region_id: str) -> Tuple[str, str, str]:
    """
    Example of region_raw: '966255053, Choudetsi, Greece'
    Returns (Region_id, Region_name, Region_country).
    """
    rid = region_id.strip()
    txt = region_raw.strip()
    name = ""
    country = ""

    if txt:
        parts = [p.strip() for p in txt.split(",")]
        if parts:
            # If the first token in raw text is numeric (OSM ID), skip it
            if parts[0].replace(" ", "").isdigit():
                if not rid:
                    rid = parts[0]
                parts = parts[1:]

            if parts:
                name = parts[0]
                if len(parts) > 1:
                    country = ", ".join(parts[1:])  # e.g., "Crete, Greece"

    return rid, name, country


def write_row(sheet, row_idx: int, region_raw: str, rid: str, name: str, country: str):
    """Write parsed region data (columns B–E) into the given row."""
    sheet.update(f"B{row_idx}:E{row_idx}", [[region_raw, rid, name, country]])


def main():
    sheet = connect_sheet()
    slugs = read_slugs(sheet)
    if not slugs:
        logging.info("No slugs found in column A.")
        return

    driver = init_driver()
    try:
        for i, slug in enumerate(slugs, start=2):  # Row 2 = first slug
            logging.info(f"[{i-1}/{len(slugs)}] Processing: {slug}")
            region_raw, region_id = get_region_from_admin(driver, slug)
            rid, name, country = parse_region(region_raw, region_id)
            write_row(sheet, i, region_raw, rid, name, country)
            time.sleep(0.3)  # avoid overloading the admin system

    finally:
        input("Done. Press Enter to close the browser…")
        driver.quit()


if __name__ == "__main__":
    main()
