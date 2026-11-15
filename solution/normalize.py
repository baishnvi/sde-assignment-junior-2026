"""Pure value-normalisation helpers. No I/O, no database: easy to unit test.

All rules here are taken literally from assignment-a-data/README.md.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def normalize_header(h: str) -> str:
    """lower-case, trim, then collapse every run of '_' / whitespace to one space."""
    return re.sub(r"[_\s]+", " ", h.lower().strip())


def normalize_phone(raw) -> str | None:
    """Return canonical '+91XXXXXXXXXX' or None if not a valid Indian mobile."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


_RE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_RE_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_RE_DASH = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_RE_MON = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")


def parse_date(raw: str) -> str | None:
    """Day-first date parser. Returns ISO 'YYYY-MM-DD' or None if invalid."""
    s = raw.strip()
    try:
        m = _RE_ISO.match(s)
        if m:
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
            return date(y, mo, d).isoformat()
        m = _RE_SLASH.match(s) or _RE_DASH.match(s)
        if m:
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
            return date(y, mo, d).isoformat()
        m = _RE_MON.match(s)
        if m:
            mo = _MONTHS.get(m[2].lower())
            if mo is None:
                return None
            return date(int(m[3]), mo, int(m[1])).isoformat()
    except ValueError:  # impossible date such as 31/02
        return None
    return None


_RE_AMOUNT = re.compile(r"^(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)$", re.IGNORECASE)


def parse_decimal(raw: str) -> str | None:
    """Parse an amount. Returns a string with exactly 2 decimals, or None if invalid.

    Rounding is ROUND_HALF_UP (what a person expects from money), not banker's rounding.
    """
    m = _RE_AMOUNT.match(raw.strip())
    if not m:
        return None
    try:
        value = Decimal(m[1].replace(",", ""))
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_enum(raw: str, values: list[str]) -> str | None:
    v = raw.strip().lower()
    allowed = {x.lower() for x in values}
    return v if v in allowed else None


def parse_event_ts(raw) -> datetime | None:
    """Parse an event timestamp to an aware UTC datetime.

    Z => UTC, '+05:30' => that offset, naive => IST (per spec).
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


def to_micros(dt: datetime) -> int:
    """Exact integer microseconds since epoch (avoids float rounding)."""
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def micros_to_ist(us: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc).astimezone(IST) + timedelta(microseconds=us)
