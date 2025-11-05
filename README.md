# 🗺️ Geo Content Automation Scripts
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Selenium](https://img.shields.io/badge/Selenium-4.x-brightgreen)
![GCP](https://img.shields.io/badge/GCP-Sheets%20API-orange)
![OSM](https://img.shields.io/badge/OpenStreetMap-API-success)

Automation toolkit for managing **geo-related content** — such as ski lifts, POIs, and resort regions — using data from **Google Sheets (via Google Cloud Platform)** and **OpenStreetMap**.

> **Stack:** Python • Selenium • GCP (Google Sheets API) • Overpass / Nominatim • Django Admin automation

This repository demonstrates Python-based automation for **geodata enrichment** and **content management systems** (Django Admin style), with an emphasis on multilingual support and spatial data accuracy.

---

## 🚀 Features

- 🔍 Fetches lift and POI coordinates from **OpenStreetMap (Overpass + Nominatim)**  
- 🧮 Reads multilingual POI data from **Google Sheets (EN/RU + grammatical cases)**  
- ⚙️ Automates data entry to admin panels using **Selenium**  
- 🌍 Configurable via `.env` — paths, credentials, and modes (headless, dry run)  
- 🪄 Keeps browser tabs open for review and debugging  

---

## 🧩 Structure

```
automatization-scripts/
│
├── ski_lifts/
│   ├── catedral_lifts_from_osm.py # Catedral Alta Patagonia (Argentina)
│   ├── garmisch_lifts_from_osm.py # Garmisch-Partenkirchen (Germany)
│   ├── gudauri_lifts_from_osm.py # Gudauri (Georgia)
│   └── admin_upload_from_sheet.py # Universal admin uploader (any resort/sheet)
│
├── GCP JSON/ # Local credentials (ignored)
│   └── geo-content-automatization-xxxx.json
│
├── .env.example # Example environment config
├── .gitignore
├── requirements.txt
└── README.md
```

---

## ⚙️ Setup

### 1. Clone the repository
```bash
git clone git@github.com:nikgalkins/automatization-scripts.git
cd automatization-scripts
```

### 2. Create a virtual environment
```bash
python -m venv venv
.\venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
Copy the example file and edit values if needed:
```bash
cp .env.example .env
```

---

## 🧭 Usage

### 🏔️ Fetch ski lift coordinates (from OSM)

Each resort script reads lift names from Google Sheets and updates
latitude, longitude, and OSM metadata (E..J columns).

```bash
# Catedral Alta Patagonia (Argentina)
python ski_lifts/catedral_lifts_from_osm.py

# Garmisch-Partenkirchen (Germany)
python ski_lifts/garmisch_lifts_from_osm.py

# Gudauri (Georgia)
python ski_lifts/gudauri_lifts_from_osm.py
```

### 🏙️ Upload POIs to Admin (universal uploader)

The universal uploader takes the sheet name and parent region as arguments.

```bash
# Example: Gudauri
python ski_lifts/admin_upload_from_sheet.py \
  --spreadsheet "POIs" \
  --worksheet "Gudauri" \
  --admin-url "https://content.ostrovok.in/admin/geo/region/add/" \
  --parent-id "6139982" \
  --parent-visible "6139982, Gudauri, GE (City)" \
  --type-visible "Point of Interest"
```

**Default behavior:**
- Parent region and type are configurable via CLI or environment variables  
- Browser runs interactively (`HEADLESS=0`)  
- Chrome window stays open after execution  

---

## 🧰 Environment Variables

| Variable | Description |
|-----------|-------------|
| `CHROME_BINARY` | Path to Chrome executable |
| `USER_DATA_DIR` | Chrome user profile for Selenium |
| `CHROMEDRIVER` | Path to ChromeDriver |
| `SERVICE_ACCOUNT_FILE` | Path to Google Service Account JSON |
| `ADMIN_URL_ADD` | Admin “Add region” page URL |
| `PARENT_SEARCH_TEXT` | Parent region numeric ID |
| `PARENT_VISIBLE_TEXT` | Parent region visible text (Select2 entry) |
| `TYPE_VISIBLE_TEXT` | Region type dropdown value (e.g., “Point of Interest”) |
| `SPREADSHEET_NAME` | Google Sheet name that stores lift/POI data |
| `WORKSHEET_NAME` | Sheet tab name within the spreadsheet |
| `DRY_RUN` | Skip Selenium actions (1 = test mode) |
| `HEADLESS` | Run Chrome invisibly (1 = headless mode) |

Use `--dry-run` to preview without Selenium.
---

## 🧪 Example Use Case

1. You maintain a Google Sheet with columns:  
   `A: Name_en | B: Name_ru | C: Genitive_ru | D: Locative_ru | F: lat | G: lon`  
2. The OSM script finds coordinates for missing items and updates the sheet.  
3. The automation script uses Selenium to add those POIs to a web admin panel automatically.  

---

## 🧠 Tech Stack

- **Python 3.10+**  
- **Selenium 4.x**  
- **gspread**, **google-auth**  
- **Overpass API**, **Nominatim**  
- **dotenv-based configuration**

---

## 👤 Author

**Nikita Galkin**  
Geo Content Automation Specialist  
📍 Batumi, Georgia  
🔗 [GitHub: nikgalkins](https://github.com/nikgalkins)

---

## 🪪 License

MIT License — for educational and portfolio use.  
(Production usage requires appropriate API credentials and access rights.)