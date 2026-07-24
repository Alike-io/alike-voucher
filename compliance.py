"""Compliance scan.

Scans a rendered voucher PDF for any TravClan / OnTrip / vendor tokens
that must not appear in a customer-facing Alike document. Runs after
every render. A pass is a hard requirement before delivery.
"""
from __future__ import annotations
import sys, re
import fitz

# Tokens that must NEVER appear in an Alike voucher output.
FORBIDDEN = [
    # vendor identifiers
    "travclan", "trav clan", "ontrip", "on trip", "on-trip",
    # vendor-side field labels stripped per SOP
    "query code", "created by",
    # vendor phones from source vouchers (only the *known* ones — do not
    # scan for generic phone-number shapes, which would false-positive on
    # our own emergency line)
    "919116037503", "9116037503",
    # vendor URLs and app CTAs
    "download ontrip", "for best travel experience",
    # currency violations — vouchers must not show ₹ pricing (tipping in
    # USD is allowed as it is on-ground guidance, not package pricing)
    "₹",
]

# Tokens Alike *requires* to appear.
REQUIRED = [
    "alike",
    "care@alike.io",
    "88000 25030",
    "Booking",
]


def scan(pdf_path: str, vendor_booking_id: str = None) -> tuple[bool, list[str]]:
    """Return (passed, findings).

    If vendor_booking_id is provided (from the extractor), we look for it
    verbatim in the rendered PDF — this catches the case where ops forgot
    to replace TravClan's booking slug with the Infinity Order ID.
    """
    findings = []
    doc = fitz.open(pdf_path)
    all_text = ""
    for page in doc:
        all_text += page.get_text().lower() + "\n"

    for token in FORBIDDEN:
        if token.lower() in all_text:
            findings.append(f"❌ FORBIDDEN token present: '{token}'")

    for token in REQUIRED:
        if token.lower() not in all_text:
            findings.append(f"❌ REQUIRED token missing: '{token}'")

    # Vendor booking ID leak check — the extractor knows what the vendor
    # slug was; if it's still in the output, ops didn't replace it.
    if vendor_booking_id and vendor_booking_id.lower() in all_text:
        findings.append(
            f"❌ Vendor booking ID '{vendor_booking_id}' still present — "
            f"replace with the Infinity Order ID before delivery."
        )

    # Extra guardrail: no vendor code-slugs. TravClan slugs are 6-char
    # lowercase alphanumeric. To keep false positives low, we only flag
    # slugs that mix letters and at least one digit (e.g. "tafs35",
    # "taqeh6", "taft1a") — this misses all-letter slugs but avoids
    # matching common English words like "ticket", "travel", "tunnel".
    slug = re.compile(r"\b(t[a-z0-9]{5})\b")
    ENGLISH_WHITELIST = {
        "ticket", "travel", "tunnel", "tigers", "tunnel", "toilet",
        "target", "temple", "tender", "tissue", "trophy",
    }
    hits = [h for h in slug.findall(all_text)
            if any(c.isdigit() for c in h) and h not in ENGLISH_WHITELIST]
    if hits:
        findings.append(f"⚠  Possible vendor slug(s) still present: {sorted(set(hits))}")

    passed = not any(f.startswith("❌") for f in findings)
    return passed, findings


if __name__ == "__main__":
    path = sys.argv[1]
    ok, notes = scan(path)
    print(f"{'PASS' if ok else 'FAIL'} — {path}")
    for n in notes:
        print("  " + n)
    sys.exit(0 if ok else 1)
