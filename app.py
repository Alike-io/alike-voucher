"""Alike Voucher Builder — Streamlit UI.

Ops flow:
  1. Upload TravClan PDF
  2. OCR runs, pre-fills a data model
  3. Ops verifies + edits the pre-filled fields, adds Infinity Booking ID,
     Travel Advisor name, and On-ground contact name+phone
  4. Click Generate → compliance scan runs → download final Alike PDF

Design decisions:
  - No API/AI calls; entirely offline (Tesseract + WeasyPrint locally)
  - JSON editor for the itinerary is intentional — the day-by-day
    structure is too irregular for a fixed form. Top-level fields, ops
    contacts, and hotels get first-class widgets.
  - Every render passes compliance.py before the download button appears.
"""
from __future__ import annotations
import io, json, tempfile, pathlib, traceback
import streamlit as st

from extractor  import extract
from renderer   import render_voucher
from compliance import scan as compliance_scan


st.set_page_config(page_title="Alike Voucher Builder", page_icon="✈", layout="wide")

# --- header ------------------------------------------------------------

st.markdown("""
<div style='background:linear-gradient(135deg,#063a2e,#0b6b4f,#12936a); padding:20px 24px; border-radius:8px; margin-bottom:20px;'>
  <div style='color:white; font-size:24px; font-weight:700;'>Alike Voucher Builder</div>
  <div style='color:rgba(255,255,255,0.85); font-size:13px; margin-top:4px;'>
    Convert TravClan B2B vouchers to Alike-branded B2C vouchers · OCR-assisted, fully offline
  </div>
</div>
""", unsafe_allow_html=True)


# --- session state -----------------------------------------------------

if "data" not in st.session_state:
    st.session_state["data"] = None
if "warnings" not in st.session_state:
    st.session_state["warnings"] = []
if "pdf_bytes" not in st.session_state:
    st.session_state["pdf_bytes"] = None
if "compliance" not in st.session_state:
    st.session_state["compliance"] = (None, [])


if "ocr_raw" not in st.session_state:
    st.session_state["ocr_raw"] = []


# --- step 1: upload ----------------------------------------------------

with st.container(border=True):
    st.subheader("1. Upload the TravClan voucher PDF")
    uploaded = st.file_uploader("Drop the source voucher here", type=["pdf"],
                                label_visibility="collapsed")
    col1, col2 = st.columns([1, 3])
    with col1:
        run_ocr = st.button("Run OCR", type="primary", disabled=(uploaded is None),
                            use_container_width=True)
    with col2:
        if uploaded:
            st.caption(f"Ready: **{uploaded.name}** ({uploaded.size/1024:.0f} KB)")

    if run_ocr and uploaded:
        with st.spinner("OCR-ing pages at 300 DPI, extracting thumbnails, and parsing…"):
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                tf.write(uploaded.read())
                tmp_path = tf.name
            # Persistent thumb dir so the renderer can reference the paths later
            thumb_dir = tempfile.mkdtemp(prefix="alike_thumbs_")
            st.session_state["thumb_dir"] = thumb_dir
            try:
                data = extract(tmp_path, thumb_dir=thumb_dir)
                st.session_state["warnings"] = data.pop("_ocr_warnings", [])
                st.session_state["ocr_raw"]  = data.pop("_ocr_pages_raw", [])
                st.session_state["vendor_booking_id"] = data.pop("_vendor_booking_id", "")
                st.session_state["data"] = data
                st.session_state["pdf_bytes"] = None
                # Alert if extraction is essentially empty
                t = data["trip"]
                empty = (not t.get("booking_id") and not t.get("hotels")
                         and not t.get("days") and not t.get("arrival"))
                total_thumbs = sum(len(s.get("thumbs", []))
                                   for d in t.get("days", [])
                                   for s in d.get("stops", []))
                if empty:
                    st.error("OCR ran, but no TravClan voucher fields were detected. "
                             "This may not be a standard TravClan booking voucher. "
                             "Expand **Show raw OCR text** below to see what OCR captured, "
                             "and share the source PDF with the tool owner to teach the "
                             "parser this format.")
                else:
                    st.success(f"OCR complete — {len(t.get('days',[]))} days, "
                               f"{len(t.get('hotels',[]))} hotels, "
                               f"{total_thumbs} thumbnails extracted. Verify the fields below.")
            except Exception as e:
                st.error(f"OCR failed: {e}")
                st.code(traceback.format_exc())
            finally:
                pathlib.Path(tmp_path).unlink(missing_ok=True)


data = st.session_state["data"]
if not data:
    st.info("Upload a TravClan PDF and click **Run OCR** to begin.")
    st.stop()

# Warnings
if st.session_state["warnings"]:
    with st.container(border=True):
        st.warning("OCR left these fields for you to confirm:")
        for w in st.session_state["warnings"]:
            st.write(f"• {w}")

# Raw OCR debug — always available after a run
if st.session_state.get("ocr_raw"):
    with st.expander("🔍 Show raw OCR text (per page)", expanded=False):
        st.caption("This is what Tesseract read off the pages. If your fields "
                   "aren't populated above, check whether the text is here — if "
                   "yes, the parser needs to learn this voucher format; if no, "
                   "the source PDF may be scanned poorly or in a language OCR "
                   "isn't tuned for.")
        for i, page_text in enumerate(st.session_state["ocr_raw"], 1):
            st.markdown(f"**Page {i}**")
            st.code(page_text or "(no text detected)", language=None)


# --- step 2: verify + edit ---------------------------------------------

trip = data["trip"]
contacts = data["contacts"]

st.subheader("2. Verify and edit")

# Top-level trip meta
with st.container(border=True):
    st.markdown("**Booking**")
    c1, c2, c3 = st.columns(3)
    trip["booking_id"] = c1.text_input("Booking ID *(paste Infinity Order ID here)*",
                                       value=trip.get("booking_id", ""),
                                       help="Ops enters the Infinity Order ID; label on the voucher stays 'Booking ID'.")
    vendor_bid = st.session_state.get("vendor_booking_id", "")
    if vendor_bid and trip["booking_id"].strip().lower() == vendor_bid.strip().lower():
        c1.warning(f"⚠ This is still the vendor's booking ID (`{vendor_bid}`). "
                   f"Replace with the Infinity Order ID before generating.")
    trip["destination"] = c2.text_input("Destination", value=trip.get("destination", ""))
    trip["nights"]      = c3.number_input("Nights", min_value=0, max_value=60,
                                          value=int(trip.get("nights") or 0))
    c4, c5, c6 = st.columns(3)
    trip["travel_date_display"] = c4.text_input("Travel Date (display)", value=trip.get("travel_date_display", ""))
    trip["pax_display"]         = c5.text_input("Pax (display)", value=trip.get("pax_display", ""))
    trip["guest_lead"]          = c6.text_input("Guest Lead (title case)", value=trip.get("guest_lead", ""))
    trip["guest_lead_first"]    = trip["guest_lead"].split()[0] if trip["guest_lead"] else ""

    st.markdown("**Guests**")
    guests_str = st.text_area("One guest per line",
                              value="\n".join(trip.get("guests", [])), height=100)
    trip["guests"] = [g.strip() for g in guests_str.split("\n") if g.strip()]


# Contacts
with st.container(border=True):
    st.markdown("**On-Ground & Contacts**")
    c1, c2 = st.columns(2)
    contacts["advisor"]         = c1.text_input("Travel Advisor (name of the person who sold the package)",
                                                value=contacts.get("advisor", ""))
    contacts["emergency"]       = c2.text_input("24×7 Emergency Number",
                                                value=contacts.get("emergency", "+91 95133 92429"))
    c3, c4, c5 = st.columns(3)
    contacts["on_ground_name"]  = c3.text_input("On-ground Support (name)",
                                                value=contacts.get("on_ground_name", ""))
    contacts["on_ground_phone"] = c4.text_input("On-ground Support (phone)",
                                                value=contacts.get("on_ground_phone", ""))
    contacts["careline"]        = c5.text_input("Careline (WhatsApp)",
                                                value=contacts.get("careline", "+91 88000 25030"))


# Arrival / Departure
with st.container(border=True):
    st.markdown("**Arrival & Departure**")

    def _flight_editor(label: str, key: str):
        val = trip.get(key) or {}
        c1, c2, c3, c4 = st.columns([1, 2, 1, 3])
        mode    = c1.text_input(f"{label} Mode", value=val.get("mode", "Flight"), key=f"{key}_m")
        date    = c2.text_input(f"{label} Date", value=val.get("date", ""), key=f"{key}_d")
        time    = c3.text_input(f"{label} Time", value=val.get("time", ""), key=f"{key}_t")
        remarks = c4.text_input(f"{label} Remarks", value=val.get("remarks") or "", key=f"{key}_r")
        if date or time or remarks:
            trip[key] = {"mode": mode, "date": date, "time": time, "remarks": remarks}
        else:
            trip[key] = None

    _flight_editor("Arrival", "arrival")
    _flight_editor("Departure", "departure")


# Hotels
with st.container(border=True):
    st.markdown("**Hotels**")
    hotels = trip.get("hotels", [])
    add, _, rm = st.columns([1, 4, 1])
    if add.button("+ Add hotel"):
        hotels.append({"name": "", "location": "", "check_in": "", "check_out": ""})
    if rm.button("− Remove last") and hotels:
        hotels.pop()

    new_hotels = []
    for i, h in enumerate(hotels):
        with st.expander(f"{i+1}. {h.get('name') or '(new hotel)'}", expanded=False):
            c1, c2 = st.columns([3, 2])
            h["name"]     = c1.text_input("Hotel name", value=h.get("name", ""), key=f"h_{i}_n")
            h["location"] = c2.text_input("Location", value=h.get("location", ""), key=f"h_{i}_l")
            c3, c4, c5 = st.columns(3)
            h["check_in"]  = c3.text_input("Check-in",  value=h.get("check_in",  ""), key=f"h_{i}_ci")
            h["check_out"] = c4.text_input("Check-out", value=h.get("check_out", ""), key=f"h_{i}_co")
            h["stars"] = c5.number_input("Stars (0=hide)", 0, 5,
                                         value=int(h.get("stars") or 0), key=f"h_{i}_s") or None
            c6, c7, c8 = st.columns(3)
            h["rooms_guests"] = c6.text_input("Rooms & Guests", value=h.get("rooms_guests", ""), key=f"h_{i}_rg")
            h["room_type"]    = c7.text_input("Room Type",      value=h.get("room_type", ""),    key=f"h_{i}_rt")
            h["meal_plan"]    = c8.text_input("Meal Plan",      value=h.get("meal_plan", ""),    key=f"h_{i}_mp")
        new_hotels.append(h)
    trip["hotels"] = [h for h in new_hotels if h.get("name")]


# Days — JSON editor (structure is too irregular for fixed widgets)
with st.container(border=True):
    st.markdown("**Day-by-Day Itinerary** — edit inline as JSON")
    st.caption("Each day is `{n, date_display, location, stops:[{title, pickup, pickup_time, drop, remarks, description}], leisure}`.")
    days_json = st.text_area("Itinerary JSON",
                             value=json.dumps(trip.get("days", []), indent=2),
                             height=380, label_visibility="collapsed")
    try:
        trip["days"] = json.loads(days_json)
        st.caption(f"✓ Valid JSON — {len(trip['days'])} days parsed.")
    except json.JSONDecodeError as e:
        st.error(f"JSON error: {e}")


# Terms
with st.container(border=True):
    st.markdown("**Terms & Conditions** — one bullet per line (kept vendor-neutral)")
    terms_str = st.text_area("T&Cs",
                             value="\n".join(trip.get("terms", [])),
                             height=280, label_visibility="collapsed")
    trip["terms"] = [t.strip() for t in terms_str.split("\n") if t.strip()]


# --- step 3: generate --------------------------------------------------

st.subheader("3. Generate the Alike voucher")

col1, col2 = st.columns([1, 4])
generate = col1.button("Generate voucher PDF", type="primary", use_container_width=True)

if generate:
    with st.spinner("Rendering PDF and running compliance scan…"):
        try:
            out_path = tempfile.mktemp(suffix=".pdf")
            render_voucher(data, out_path)
            ok, findings = compliance_scan(out_path,
                vendor_booking_id=st.session_state.get("vendor_booking_id") or None)
            with open(out_path, "rb") as f:
                st.session_state["pdf_bytes"] = f.read()
            st.session_state["compliance"] = (ok, findings)
            pathlib.Path(out_path).unlink(missing_ok=True)
        except Exception as e:
            st.error(f"Render failed: {e}")
            st.code(traceback.format_exc())


ok, findings = st.session_state["compliance"]
if st.session_state["pdf_bytes"]:
    if ok:
        st.success("✓ Compliance scan PASSED — no vendor identifiers detected.")
    else:
        st.error("✗ Compliance scan FAILED:")
        for f in findings:
            st.write(f)
    # Show warnings even on pass
    warn_only = [f for f in findings if not f.startswith("❌")]
    if warn_only and ok:
        for f in warn_only:
            st.warning(f)

    # Filename
    guest_last = (trip.get("guest_lead", "").split() or ["Guest"])[-1]
    dest_slug  = (trip.get("destination", "Dest") or "Dest").replace(" ", "").replace("/", "-")
    booking    = trip.get("booking_id", "REF")
    fname = f"Alike_{dest_slug}_{booking}_{guest_last}.pdf"

    st.download_button("↓ Download Alike voucher",
                       data=st.session_state["pdf_bytes"],
                       file_name=fname,
                       mime="application/pdf",
                       type="primary",
                       disabled=not ok)
