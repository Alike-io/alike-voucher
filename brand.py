"""Alike brand tokens per Alike_Project_Guide.md.

Central lookup so the renderer picks the right gradient/accent for the
destination on the voucher.
"""

ORANGE = "#ec601d"
INK = "#18181b"

DESTINATION_GRADIENTS = {
    "bali":      ("#003d4d", "#006d8f", "#00897b"),
    "vietnam":   ("#063a2e", "#0b6b4f", "#12936a"),
    "thailand":  ("#0a3d62", "#1e7898", "#2bb3c0"),
    "sri lanka": ("#053b3a", "#0f7d6e", "#20b59a"),
    "singapore": ("#06234d", "#0e5a8a", "#1f8f86"),
    # sensible default for anything else
    "default":   ("#0a2540", "#155a80", "#1e88a8"),
}


def gradient_for(destination: str) -> tuple[str, str, str]:
    """Match on the first destination word; case-insensitive."""
    if not destination:
        return DESTINATION_GRADIENTS["default"]
    key = destination.strip().lower()
    for name, stops in DESTINATION_GRADIENTS.items():
        if name in key:
            return stops
    return DESTINATION_GRADIENTS["default"]
