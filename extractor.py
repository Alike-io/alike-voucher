"""OCR-based extractor for TravClan vouchers.

The source PDFs have no usable text layer (Qt-rendered with Identity-H
encoding that breaks reverse-mapping to Unicode). We rasterize each
page at 300 DPI and run Tesseract, then parse structured fields out of
the OCR text with heuristics and regex.

Output shape matches what the renderer expects. Ops verifies and
corrects the pre-filled values in the Streamlit form before rendering.
"""
from __future__ import annotations
import io, re
from typing import Optional, Tuple, List
import fitz
import pytesseract
from PIL import Image


# --- OCR ---------------------------------------------------------------

def ocr_pdf(pdf_path: str, dpi: int = 300) -> List[str]:
    """Return one OCR string per page."""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        pages.append(pytesseract.image_to_string(img))
    doc.close()
    return pages


# --- helpers -----------------------------------------------------------

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
DATE   = rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTHS},?\s*\d{{4}}"
TIME   = r"\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)"


def _first(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# --- field parsers -----------------------------------------------------

def parse_booking_meta(text: str) -> dict:
    d = {}
    d["vendor_booking_id"] = _first(r"Booking\s*Id[:\s]+([A-Za-z0-9]+)", text)
    d["vendor_query_code"] = _first(r"Query\s*Code[:\s]+([A-Za-z0-9]+)", text)
    d["travel_date"]       = _first(rf"Travel\s*Date[:\s]+({DATE})", text)
    d["nights"]            = _first(r"No\s*of\s*Nights[:\s]+(\d+)", text)
    d["destination"]       = _first(r"Destination[:\s]+([A-Za-z /]+?)(?:\n|Pax|$)", text)
    d["pax"]               = _first(r"Pax[:\s]+(\d+\s*Adult(?:s)?(?:\s*[|,]\s*\d+\s*Child(?:ren)?)?)", text)

    m = re.search(r"([A-Z][A-Z\s]{2,})'s\s+[A-Z][a-z]+", text)
    if m:
        d["guest_lead"] = _clean(m.group(1)).title()
    return d


def parse_guests(text: str) -> List[str]:
    m = re.search(r"Guests?\s*Details\s*(.+?)(?:Arrival|Hotels|Itinerary|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    lines = re.findall(r"^\s*\d+[\.\)]\s*(.+?)$", block, re.MULTILINE)
    return [_clean(l) for l in lines if l.strip()]


def parse_flights(text: str) -> Tuple[Optional[dict], Optional[dict]]:
    """TravClan lays arrival + departure side-by-side. OCR reads both
    columns on the same line, so we slice each line into left/right at
    the widest run of spaces and grab date/time/remarks from each half."""
    m = re.search(r"Arrival\s*&\s*Departure\s*Details\s*(.+?)(?:Hotels|Itinerary|Guests|Terms|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None, None
    block = m.group(1)

    date_time_line = re.search(rf"({DATE})\s*\|\s*({TIME}).*?({DATE})?\s*\|?\s*({TIME})?",
                                block, re.IGNORECASE)
    remarks_line   = re.search(r"Remarks\s*-\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)

    def split_remarks(s: str) -> tuple[str, str]:
        # Slice on 'Remarks -' or the widest gap
        parts = re.split(r"\s*Remarks\s*-\s*", s, flags=re.IGNORECASE)
        if len(parts) >= 2:
            return _clean(parts[0]), _clean(parts[1])
        # Fallback: split on wide gap
        m2 = re.search(r"\s{2,}", s)
        if m2:
            return _clean(s[:m2.start()]), _clean(s[m2.end():])
        return _clean(s), ""

    dt_pairs = re.findall(rf"({DATE})\s*\|\s*({TIME})", block)
    remarks_full = None
    rm = re.search(r"Remarks\s*-\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)
    if rm:
        remarks_full = rm.group(1)
    r_left, r_right = split_remarks(remarks_full) if remarks_full else ("", "")

    def norm_remarks(r: str) -> Optional[str]:
        if not r:
            return None
        r = re.sub(r"\s*\|\|\s*", " · ", r)
        r = re.sub(r"\s+\|\s+", " · ", r)
        r = re.sub(r"\s+", " ", r).strip()
        return r or None

    def build(idx, remarks_str):
        if idx >= len(dt_pairs):
            return None
        d, t = dt_pairs[idx]
        return {"mode": "Flight", "date": _clean(d), "time": _clean(t),
                "remarks": norm_remarks(remarks_str)}

    return build(0, r_left), build(1, r_right)


def parse_hotels(text: str) -> List[dict]:
    """TravClan formats vary: sometimes hotel name is on the same line as
    'Check in - date' (Jash pattern), sometimes 1-2 lines above (Nilesh
    pattern). Anchor on each 'Check in - DATE' line and walk backward
    through preceding lines to find the first name-like line."""
    m = re.search(r"Hotels\s*(.+?)(?:Trip\s*Itinerary|Itinerary|Terms|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    lines = block.split("\n")

    check_in_re  = re.compile(rf"Check\s*in\s*-\s*({DATE}(?:\s*\|\s*{TIME})?)", re.IGNORECASE)
    check_out_re = re.compile(r"Check\s*out\s*-\s*([^\n]+)", re.IGNORECASE)

    def is_noise(l):
        s = l.strip()
        if not s:
            return True
        if len(s) < 12 and re.fullmatch(r"[k\*e\u2605\u25c6\s\|tK,;.]+", s, re.IGNORECASE):
            return True
        if re.match(r"^\s*Confirmation\s*No", s, re.IGNORECASE):
            return True
        if re.match(r"^\s*(Rooms?|Room Type|Meal Type|Remarks)\b", s, re.IGNORECASE):
            return True
        if re.match(r"^\s*Check\s*(in|out)\b", s, re.IGNORECASE):
            return True
        return False

    def looks_like_name(l):
        s = re.sub(r"\s*Confirmation\s*No[^\n]*$", "", l, flags=re.IGNORECASE).strip()
        s = re.sub(r"^[^A-Za-z0-9]+", "", s).strip()
        if len(s) < 4:
            return False
        return sum(c.isalpha() for c in s) >= 4 and any(c.isupper() for c in s)

    hotels = []
    for idx, ln in enumerate(lines):
        mm = check_in_re.search(ln)
        if not mm:
            continue
        before = ln[:mm.start()].strip()
        name = None
        if before and not is_noise(before) and looks_like_name(before):
            name = re.sub(r"\s*Confirmation\s*No[^\n]*$", "", before, flags=re.IGNORECASE).strip()
            name = re.sub(r"^[^A-Za-z0-9]+", "", name).strip()
        if not name:
            for k in range(idx - 1, max(idx - 6, -1), -1):
                prev = lines[k].strip()
                if not prev or is_noise(prev):
                    continue
                if looks_like_name(prev):
                    name = re.sub(r"\s*Confirmation\s*No[^\n]*$", "", prev, flags=re.IGNORECASE).strip()
                    name = re.sub(r"^[^A-Za-z0-9]+", "", name).strip()
                    break
        if not name:
            continue

        h = {"name": _clean(name), "check_in": _clean(mm.group(1))}

        forward = lines[idx+1:idx+16]
        for i2, l in enumerate(forward):
            if check_in_re.search(l):
                forward = forward[:i2]
                break

        for l in forward:
            if not l.strip():
                continue
            mco = check_out_re.search(l)
            if mco and "check_out" not in h:
                h["check_out"] = _clean(mco.group(1)); continue
            mr = re.search(r"Rooms?\s*&\s*Guests?\s*-\s*(.+)", l, re.IGNORECASE)
            if mr and "rooms_guests" not in h:
                h["rooms_guests"] = _clean(mr.group(1)).replace("|", "\u00b7"); continue
            mt = re.search(r"Room\s*Type\s*-\s*(.+)", l, re.IGNORECASE)
            if mt and "room_type" not in h:
                h["room_type"] = _clean(mt.group(1)); continue
            mp = re.search(r"Meal\s*Type\s*-\s*([A-Z]{2,3})", l, re.IGNORECASE)
            if mp and "meal_plan" not in h:
                code = _clean(mp.group(1)).upper()
                h["meal_plan"] = {"CP": "Breakfast", "MAP": "Half Board",
                                  "AP": "Full Board", "EP": "Room Only"}.get(code, code)
                continue
            if "location" not in h and not is_noise(l):
                if " - " not in l:
                    loc = re.sub(r"^[^A-Za-z0-9]+", "", l).strip()
                    if 2 < len(loc) < 60 and any(c.isalpha() for c in loc):
                        h["location"] = _clean(loc)
        hotels.append(h)
    return hotels

def parse_days(text: str) -> List[dict]:
    m = re.search(r"(?:Trip\s+)?Itinerary\s*(.+?)(?:Terms?\s*&\s*Conditions?|Thank you|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)

    day_pattern = re.compile(
        rf"[\|\s]*Day\s+(\d+):\s*([A-Za-z]{{3}}[a-z]*),?\s*({DATE})",
        re.IGNORECASE)
    matches = list(day_pattern.finditer(block))
    days = []
    for i, dm in enumerate(matches):
        n = int(dm.group(1))
        weekday = dm.group(2)
        date_part = _clean(dm.group(3))
        date_display = f"{weekday}, {date_part}"
        start = dm.end()
        end = matches[i+1].start() if i + 1 < len(matches) else len(block)
        chunk = block[start:end]

        if re.search(r"\bleisure\b", chunk, re.IGNORECASE):
            days.append({"n": n, "date_display": date_display, "leisure": True, "stops": []})
            continue

        stops = _parse_day_stops(chunk)
        days.append({"n": n, "date_display": date_display, "stops": stops})
    return days


def _parse_day_stops(chunk: str) -> List[dict]:
    stops = []
    pickup_positions = [m.start() for m in re.finditer(r"Pickup\s*from", chunk, re.IGNORECASE)]
    if not pickup_positions:
        title = _first(r"^\s*([A-Z][^\n]{5,150})$", chunk, re.MULTILINE)
        if title:
            stops.append({"title": _clean(title)})
        return stops

    prev_end = 0
    for j, pos in enumerate(pickup_positions):
        next_pos = pickup_positions[j+1] if j+1 < len(pickup_positions) else len(chunk)
        title_region = chunk[prev_end:pos]
        title_candidates = [l.strip() for l in title_region.split("\n") if l.strip()]
        title = ""
        for l in reversed(title_candidates):
            if l.startswith("Remarks") or "Pickup" in l or "Drop at" in l:
                continue
            if len(l) < 6:
                continue
            if re.fullmatch(r"[^\w]+", l):
                continue
            title = re.sub(r"^[^\w\d]+", "", l)
            title = re.sub(r"\s*\|\s*\d+\s*Cab\(?s?\)?\s*$", "", title)
            title = _clean(title)
            break

        sub = chunk[pos:next_pos]
        pickup = _first(r"Pickup\s*from[:\s]+([^\n]+?)(?:\s{2,}(?:ay|=|>|\S)\s*Drop\s*at|$)", sub) \
              or _first(r"Pickup\s*from[:\s]+([^\n]+)", sub)
        drop   = _first(r"Drop\s*at[:\s]+([^\n]+)", sub)
        if pickup: pickup = re.sub(r"\s+[^\w]+$", "", pickup).strip()
        if drop:   drop   = re.sub(r"\s+[^\w]+$", "", drop).strip()

        pickup_time = _first(r"Pickup\s*time\s*-\s*([^\n]+)", sub)
        remarks     = _first(r"Remarks:\s*([^\n]+)", sub)

        desc = None
        # Grab prose paragraphs after Remarks (or after Pickup time)
        anchor_match = re.search(r"(?:Remarks:[^\n]+|Pickup\s*time[^\n]+)\n\s*\n([A-Z][^\n]{40,}(?:\n[^\n]+)*?)(?=\n\s*\n|\Z)",
                                  sub, re.DOTALL)
        if anchor_match:
            desc = _clean(anchor_match.group(1))

        stop = {"title": title}
        if pickup: stop["pickup"] = _clean(pickup)
        if pickup_time: stop["pickup_time"] = _clean(pickup_time)
        if drop: stop["drop"] = _clean(drop)
        if remarks: stop["remarks"] = _clean(remarks)
        if desc: stop["description"] = desc
        stops.append(stop)
        prev_end = next_pos
    return stops


def parse_terms(text: str) -> List[str]:
    m = re.search(r"Terms?\s*&\s*Conditions?\s*(.+?)(?:Thank you|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    items = re.split(r"\n\s*(?:[e\*•◆\-–]|\d+\.)\s+", block)
    out = []
    for item in items:
        it = _clean(item)
        for sub in re.split(r"\s{2,}(?=[A-Z][a-z])", it):
            sub = _clean(sub)
            if 15 < len(sub) < 600 and not sub.lower().startswith(("thank", "have a safe")):
                out.append(sub)
    return out


def extract(pdf_path: str) -> dict:
    pages = ocr_pdf(pdf_path)
    text = "\n".join(pages)

    meta   = parse_booking_meta(text)
    arrival, departure = parse_flights(text)
    hotels = parse_hotels(text)
    days   = parse_days(text)
    terms  = parse_terms(text)
    guests = parse_guests(text)

    dest = meta.get("destination", "") or ""
    guest_lead = meta.get("guest_lead", "")
    guest_lead_first = guest_lead.split()[0] if guest_lead else ""

    return {
        "trip": {
            "booking_id":          meta.get("vendor_booking_id", ""),
            "destination":         dest,
            "guest_lead":          guest_lead,
            "guest_lead_first":    guest_lead_first,
            "travel_date_display": meta.get("travel_date", ""),
            "nights":              int(meta.get("nights", 0)) if meta.get("nights") else 0,
            "pax_display":         meta.get("pax", ""),
            "guests":              guests,
            "arrival":             arrival,
            "departure":           departure,
            "hotels":              hotels,
            "days":                days,
            "terms":               terms,
        },
        "contacts": {
            "advisor":         "",
            "on_ground_name":  "",
            "on_ground_phone": "",
            "emergency":       "+91 95133 92429",
            "careline":        "+91 88000 25030",
        },
        "_ocr_pages_raw": pages,
        "_ocr_warnings":  _sanity_check(meta, hotels, days),
    }


def _sanity_check(meta, hotels, days) -> List[str]:
    w = []
    if not meta.get("vendor_booking_id"):
        w.append("Booking ID not detected — verify and replace with Infinity Order ID.")
    if not meta.get("destination"):
        w.append("Destination not detected — set manually.")
    if not meta.get("travel_date"):
        w.append("Travel date not detected — set manually.")
    if not hotels:
        w.append("No hotels detected — verify (some vouchers legitimately have zero hotels).")
    if not days:
        w.append("No day-by-day itinerary detected — this is unusual.")
    return w


if __name__ == "__main__":
    import sys, json
    result = extract(sys.argv[1])
    result.pop("_ocr_pages_raw", None)
    print(json.dumps(result, indent=2, default=str))
