# Alike Voucher Builder

Converts TravClan B2B booking vouchers into Alike-branded B2C vouchers.
Fully offline — no API calls, no LLM, no external service dependencies.

## What it does

1. Ops uploads the TravClan voucher PDF.
2. Local OCR (Tesseract) extracts booking metadata, guests, hotels, itinerary and T&Cs.
3. Ops verifies the pre-filled fields, enters the **Infinity Booking ID**, the
   **Travel Advisor** name, and the **On-Ground executive**'s name + phone.
4. A compliance scan verifies zero vendor identifiers leak through.
5. The final Alike voucher PDF downloads to the ops machine.

## Install

**System dependencies** (Ubuntu / Debian; equivalents on macOS and Windows):

```bash
sudo apt install tesseract-ocr libpango-1.0-0 libpangoft2-1.0-0
```

**Python** (3.10+):

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

A browser tab opens at `http://localhost:8501`. Drag a TravClan voucher in, verify, hit **Generate**, download.

## Deploy on Streamlit Cloud

The repo already contains `packages.txt` (system dependencies) and
`requirements.txt` (Python dependencies), so a one-click deploy from
GitHub works. Streamlit Cloud reads `packages.txt` before pip runs and
installs the WeasyPrint + Tesseract system libraries. No additional
configuration required.

If the app crashes with `OSError: cannot load library 'libgobject-2.0-0'`
or similar, verify `packages.txt` is at the repo root (not inside a
subfolder) and reboot the app from the Streamlit Cloud dashboard.

## Project layout

```
alike_voucher_tool/
├── app.py                 # Streamlit UI
├── extractor.py           # OCR + parsing
├── renderer.py            # HTML → PDF (WeasyPrint)
├── compliance.py          # forbidden/required token scan
├── brand.py               # per-destination gradient tokens
├── templates/
│   └── voucher.html       # Jinja2 template
├── static/
│   ├── fonts/             # Poppins (Regular/Medium/SemiBold/Bold)
│   └── img/               # transparent Alike logos (white + dark-text)
├── samples/               # test data + extracted TravClan thumbnails
├── build/                 # generated PDFs (git-ignored)
└── requirements.txt
```

## Design decisions locked with the PM

| # | Decision | Value |
|---|---|---|
| D1 | Data ingestion | **OCR-assisted form** (Tesseract pre-fill + ops verify) |
| D2 | Delivery form | **Streamlit** (offline, no API, no AI) |
| D3 | Kept from source | emergency line, compulsory-tipping T&C, full T&Cs, hotel & activity thumbnails |
| D3 | Dropped from source | OnTrip App banner, Query Code, vendor phones, USD pricing, TravClan/Confirmation IDs, star icons, liability boilerplate |
| D4 | Ops fields on voucher | Booking ID (free text = Infinity Order ID), Travel Advisor, On-ground Exec (name + phone), 24×7 Emergency, Careline |

## Compliance guarantees

Every generated PDF is scanned before download. Blocked tokens include:
`travclan`, `ontrip`, `on trip`, `query code`, `download ontrip`,
`for best travel experience`, TravClan phone `919116037503`, and any
6-char alnum vendor booking slug that mixes letters and digits.
Required tokens: `alike`, `care@alike.io`, `88000 25030`, `Booking`.

The download button is disabled if the scan fails.

## Voucher formats supported

The tool handles the full observed variance in TravClan vouchers:
- 4 to 10+ day itineraries
- 0 to 5+ hotels
- Single or multi-destination trips
- With or without flights, transfers or activities
- Optional descriptions and thumbnails per stop

## Troubleshooting

- **`weasyprint` errors about missing libraries** — install the pango + gobject
  system packages (see Install above).
- **Fonts look wrong** — verify `static/fonts/Poppins-*.ttf` exist and are ≥100 KB.
  A silent partial download will produce ~84-byte files; delete and re-fetch.
- **Logo appears as a black box** — the tool regenerates a transparent variant on
  first render. If it persists, delete `static/img/alike_*.png` and re-clone.
- **OCR is slow** — first-time page is ~4-6s; a full 5-page voucher takes ~25s.
  This is Tesseract at 300 DPI; runs on any laptop.
