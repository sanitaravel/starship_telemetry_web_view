"""Field classification and parsing of recognised OCR text.

Maps each field name to its kind (time, numeric value, unit, free text), the
EasyOCR allowlist for that kind, and the parser that turns the recognised
string into a value.
"""

import re


# Time format regex: HH:MM:SS (no sign prefix, just digits and colons)
_TIME_PATTERN = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")

# Signed clock: "T- 00:12:31", "T+00:00:05", "-00:00:27"; the sign is optional
_SIGNED_TIME_PATTERN = re.compile(r"([+\-−])?\s*(\d{1,2}):(\d{2}):(\d{2})")

# Numeric value regex: optional sign, digits with optional decimal point
_NUMERIC_PATTERN = re.compile(r"[+\-−]?\s*(\d+(?:[.,]\d+)?)")

# Allowed character sets for EasyOCR per field type
_ALLOWLIST_TIME = "0123456789:_"
_ALLOWLIST_NUMERIC = "0123456789."
_ALLOWLIST_UNIT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ/"
# The countdown clock with its "T-" and the HUD clock's sign share one list,
# so before T-0 they cost the CPU path a single extra recogniser call
_ALLOWLIST_SIGNED_TIME = "0123456789:T+-"

# The pre-launch countdown clock (top-left) and the sign left of the HUD clock
COUNTDOWN_FIELD = "countdown_time"
TIME_SIGN_FIELD = "time_sign"

# Recognised unit spellings, mapped to their canonical form. The slash in
# "KM/H" is often read as Z, I, L or 1, or dropped entirely.
_SPEED_UNIT_PATTERN = re.compile(r"KM[/ZIL1|]?H")
_SPEED_UNITS = {"MPH": "MPH"}
_ALTITUDE_UNITS = {"KM": "KM", "MI": "MI", "FT": "FT"}


def parse_time_value(text: str) -> str | None:
    """Parse mission elapsed time from OCR text.

    Expected format: HH:MM:SS (digits and colons only).
    Returns the raw time string as-is, or None if parsing fails.
    """
    match = _TIME_PATTERN.search(text)
    if not match:
        return None

    hours = match.group(1).zfill(2)
    minutes = match.group(2)
    seconds = match.group(3)

    # Validate ranges
    if int(minutes) > 59 or int(seconds) > 59:
        return None

    return f"{hours}:{minutes}:{seconds}"


def parse_signed_time_value(text: str) -> str | None:
    """Parse a countdown clock such as ``"T- 00:12:31"`` into ``"-00:12:31"``.

    The countdown clock only ever counts down to T-0, so a reading whose sign
    was not recognised is taken as negative; an explicit ``+`` is kept.
    Returns None when no valid HH:MM:SS is found.
    """
    match = _SIGNED_TIME_PATTERN.search(text)
    if not match:
        return None
    sign, hours, minutes, seconds = match.groups()
    if int(minutes) > 59 or int(seconds) > 59:
        return None
    return f"{'+' if sign == '+' else '-'}{hours.zfill(2)}:{minutes}:{seconds}"


def parse_sign_value(text: str) -> str | None:
    """The ``+`` or ``-`` of a ``T+``/``T-`` read left of the HUD clock, or None."""
    has_plus = "+" in text
    has_minus = "-" in text or "−" in text
    if has_plus == has_minus:
        return None
    return "+" if has_plus else "-"


def parse_unit_value(field_name: str, text: str) -> str | None:
    """Return the canonical unit for a unit field, or None if it is not one.

    Speed units must look like KM/H or MPH, altitude units like KM, MI or FT.
    Anything else (e.g. text OCR'd from the engine graphic that replaces a
    gauge before stage separation) is rejected.
    """
    normalized = re.sub(r"\s+", "", text).upper()
    if field_name.startswith("speed"):
        if _SPEED_UNIT_PATTERN.fullmatch(normalized):
            return "KM/H"
        return _SPEED_UNITS.get(normalized)
    return _ALTITUDE_UNITS.get(normalized)


def parse_numeric_value(text: str) -> float | None:
    """Parse a numeric value (speed or altitude) from OCR text.

    Handles integers and decimals. Returns None if no numeric value found.
    """
    match = _NUMERIC_PATTERN.search(text)
    if not match:
        return None

    # Replace comma with dot for decimal parsing
    num_str = match.group(1).replace(",", ".")
    try:
        return float(num_str)
    except ValueError:
        return None


def is_time_field(field_name: str) -> bool:
    """Check if a field name corresponds to the time region."""
    return field_name == "time"


def is_numeric_field(field_name: str) -> bool:
    """Check if a field name corresponds to a numeric value (speed/altitude)."""
    numeric_fields = {
        "speed_l",
        "speed_r",
        "altitude_l",
        "altitude_r",
    }
    return field_name in numeric_fields


def is_unit_field(field_name: str) -> bool:
    """Check if a field name corresponds to a unit region."""
    unit_fields = {
        "speed_l_unit",
        "speed_r_unit",
        "altitude_l_unit",
        "altitude_r_unit",
    }
    return field_name in unit_fields


def is_signed_time_field(field_name: str) -> bool:
    """The countdown clock and the HUD clock's sign: read with their T-/T+."""
    return field_name in (COUNTDOWN_FIELD, TIME_SIGN_FIELD)


def get_allowlist(field_name: str) -> str | None:
    """Return the EasyOCR allowlist for a given field, or None for no restriction."""
    if is_time_field(field_name):
        return _ALLOWLIST_TIME
    elif is_signed_time_field(field_name):
        return _ALLOWLIST_SIGNED_TIME
    elif is_numeric_field(field_name):
        return _ALLOWLIST_NUMERIC
    elif is_unit_field(field_name):
        return _ALLOWLIST_UNIT
    return None


def parse_field_value(field_name: str, raw_text: str) -> float | str | None:
    """Parse the raw OCR text based on field type.

    Args:
        field_name: Name of the field to determine parsing strategy
        raw_text: Raw text extracted by EasyOCR

    Returns:
        Parsed value (float for numeric, str for time/text) or None
    """
    if is_time_field(field_name):
        return parse_time_value(raw_text)
    elif field_name == COUNTDOWN_FIELD:
        return parse_signed_time_value(raw_text)
    elif field_name == TIME_SIGN_FIELD:
        return parse_sign_value(raw_text)
    elif is_numeric_field(field_name):
        return parse_numeric_value(raw_text)
    elif is_unit_field(field_name):
        return parse_unit_value(field_name, raw_text)
    else:
        # Stage labels and the HUD milestone labels are kept as strings
        return raw_text
