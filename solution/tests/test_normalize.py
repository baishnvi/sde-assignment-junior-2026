import pytest

from solution.normalize import (micros_to_ist, normalize_header, normalize_phone, parse_date,
                                parse_decimal, parse_enum, parse_event_ts, to_micros)


@pytest.mark.parametrize("raw,expected", [
    ("9876543210", "+919876543210"),
    ("+91 98765 43210", "+919876543210"),
    ("09876543210", "+919876543210"),
    ("91-98765-43210", "+919876543210"),
    ("(+91) 9876543210", "+919876543210"),
    ("+919876543210", "+919876543210"),
    ("  98765 43210  ", "+919876543210"),
])
def test_phone_valid_formats(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    "", None, "N/A", "987654321",            # 9 digits
    "5876543210",                            # bad prefix
    "022-2345-6789",                         # landline: 0 + 10 digits starting with 2
    "+1 415 555 0134",                       # foreign
    "919876543210 99",                       # too long
    "0919876543210",                         # 0 and 91 are not both stripped
])
def test_phone_invalid(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("2026-12-01", "2026-12-01"),
    ("01/12/2026", "2026-12-01"),          # day first, not month first
    ("01-12-2026", "2026-12-01"),
    ("01-Dec-2026", "2026-12-01"),
    ("01-dEc-2026", "2026-12-01"),         # month name is case-insensitive
    ("29/02/2028", "2028-02-29"),          # leap day
])
def test_date_valid(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "29/02/2027", "2026-13-01", "13/13/2026", "soon", "TBD",
                                 "01-Foo-2026", "2026/12/01", ""])
def test_date_invalid(raw):
    assert parse_date(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("27554", "27554.00"), ("₹27,554", "27554.00"), ("Rs. 25301", "25301.00"), ("rs 25301.5", "25301.50"),
    ("INR 25301.4", "25301.40"), ("inr25301", "25301.00"), ("Rs.1,00,000", "100000.00"),
    ("0", "0.00"), ("12.345", "12.35"), ("12.344", "12.34"), ("₹ 10", "10.00"),
])
def test_decimal_valid(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["N/A", "-", "twelve thousand", "-5", "Rs", "", "$10", "1.2.3"])
def test_decimal_invalid(raw):
    assert parse_decimal(raw) is None


def test_enum_case_insensitive_and_invalid():
    vals = ["en", "hi"]
    assert parse_enum("HI", vals) == "hi"
    assert parse_enum(" en ", vals) == "en"
    assert parse_enum("Hindi", vals) is None


def test_header_normalisation():
    assert normalize_header("  Policy_No ") == "policy no"
    assert normalize_header("Mobile   NO") == "mobile no"
    assert normalize_header("policy__number") == "policy number"


def test_event_timestamps_three_forms_agree():
    z = parse_event_ts("2026-10-03T04:31:00Z")
    off = parse_event_ts("2026-10-03T10:01:00+05:30")
    naive = parse_event_ts("2026-10-03 10:01:00")  # naive means IST
    assert z == off == naive
    assert parse_event_ts("garbage") is None
    assert parse_event_ts(None) is None


def test_ist_date_boundary():
    # 2026-10-03T19:00:00Z is 2026-10-04 00:30 IST: reports must use the IST date
    us = to_micros(parse_event_ts("2026-10-03T19:00:00Z"))
    assert micros_to_ist(us).date().isoformat() == "2026-10-04"
