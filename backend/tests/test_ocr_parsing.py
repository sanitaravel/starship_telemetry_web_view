"""Tests for parsing recognised OCR text (src.ocr.parsing).

Example-based tests for the time, numeric and unit parsers, plus property tests
for numeric and time parsing.
"""

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import composite

from src.ocr.parsing import parse_numeric_value, parse_time_value, parse_unit_value


# --- Parsing Tests ---


class TestParseTimeValue:
    def test_standard(self):
        assert parse_time_value("01:23:45") == "01:23:45"

    def test_t_zero(self):
        assert parse_time_value("00:00:00") == "00:00:00"

    def test_with_noise(self):
        assert parse_time_value("  T+00:02:35  ") == "00:02:35"

    def test_single_digit_hour(self):
        assert parse_time_value("1:02:03") == "01:02:03"

    def test_minutes_out_of_range(self):
        assert parse_time_value("00:60:00") is None

    def test_seconds_out_of_range(self):
        assert parse_time_value("00:00:61") is None

    def test_invalid_format(self):
        assert parse_time_value("12:34") is None


class TestParseNumericValue:
    def test_integer(self):
        assert parse_numeric_value("1523") == 1523.0

    def test_decimal(self):
        assert parse_numeric_value("48.2") == 48.2

    def test_with_comma_decimal(self):
        assert parse_numeric_value("1,5") == 1.5

    def test_with_surrounding_noise(self):
        assert parse_numeric_value("  2100.5 km/h") == 2100.5

    def test_no_number(self):
        assert parse_numeric_value("KM/H") is None

    def test_empty_string(self):
        assert parse_numeric_value("") is None


class TestParseUnitValue:
    @pytest.mark.parametrize("text", ["KM/H", "km/h", "KMZH", "KMIH", "KMH", "KM / H"])
    def test_speed_unit_normalised(self, text):
        assert parse_unit_value("speed_r_unit", text) == "KM/H"

    def test_mph(self):
        assert parse_unit_value("speed_l_unit", "mph") == "MPH"

    def test_altitude_unit(self):
        assert parse_unit_value("altitude_r_unit", "KM") == "KM"

    @pytest.mark.parametrize("text", ["Lod", "Zod", "PKM", "F"])
    def test_garbage_rejected(self, text):
        assert parse_unit_value("speed_l_unit", text) is None
        assert parse_unit_value("altitude_l_unit", text) is None

    def test_speed_unit_not_valid_for_altitude(self):
        assert parse_unit_value("altitude_l_unit", "KM/H") is None


# ─── Property-Based Tests ─────────────────────────────────────────────────────


# ─── Strategies ───────────────────────────────────────────────────────────────


@composite
def valid_integer_string(draw):
    """Strategy for generating valid integer strings."""
    value = draw(st.integers(min_value=0, max_value=99999))
    return str(value), float(value)


@composite
def valid_decimal_string(draw):
    """Strategy for generating valid decimal strings."""
    integer_part = draw(st.integers(min_value=0, max_value=9999))
    decimal_part = draw(st.integers(min_value=0, max_value=99))
    decimal_str = f"{integer_part}.{decimal_part}"
    expected_value = float(decimal_str)
    return decimal_str, expected_value


@composite
def valid_time_string(draw):
    """Strategy for generating valid HH:MM:SS strings."""
    hours = draw(st.integers(min_value=0, max_value=99))
    minutes = draw(st.integers(min_value=0, max_value=59))
    seconds = draw(st.integers(min_value=0, max_value=59))
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class TestPropertyParsing:
    """Property 8: Numeric and Time Parsing Correctness.

    For any valid numeric string (integer or decimal), parsing SHALL produce a
    float equal to the original value. For any valid clock reading HH:MM:SS,
    parsing SHALL return the same string.

    **Validates: Requirements 4.4, 4.5**
    """

    @given(data=valid_integer_string())
    @settings(max_examples=100)
    def test_integer_parsing_produces_correct_float(self, data):
        """Parsing any valid integer string produces the equivalent float value."""
        text, expected = data
        result = parse_numeric_value(text)
        assert result is not None
        assert result == expected

    @given(data=valid_decimal_string())
    @settings(max_examples=100)
    def test_decimal_parsing_produces_correct_float(self, data):
        """Parsing any valid decimal string produces the equivalent float value."""
        text, expected = data
        result = parse_numeric_value(text)
        assert result is not None
        assert abs(result - expected) < 1e-9

    @given(time_str=valid_time_string())
    @settings(max_examples=100)
    def test_time_parsing_round_trips(self, time_str):
        """Parsing a valid HH:MM:SS string round-trips to the same string."""
        assert parse_time_value(time_str) == time_str

    @given(time_str=valid_time_string())
    @settings(max_examples=100)
    def test_time_parsing_produces_valid_format(self, time_str):
        """Parsed result always matches HH:MM:SS format."""
        result = parse_time_value(time_str)
        assert result is not None
        assert re.match(r"^\d{2}:\d{2}:\d{2}$", result)

    @given(
        integer_part=st.integers(min_value=0, max_value=9999),
        decimal_part=st.integers(min_value=0, max_value=99),
    )
    @settings(max_examples=100)
    def test_comma_decimal_parsed_same_as_dot_decimal(self, integer_part, decimal_part):
        """Comma-separated decimals are parsed identically to dot-separated ones."""
        dot_str = f"{integer_part}.{decimal_part}"
        comma_str = f"{integer_part},{decimal_part}"
        dot_result = parse_numeric_value(dot_str)
        comma_result = parse_numeric_value(comma_str)
        assert dot_result is not None
        assert comma_result is not None
        assert abs(dot_result - comma_result) < 1e-9
