"""Tests for module-level helper functions."""

import pytest
from fidelity.api_client import _parse_float, _parse_int, _get_put_call, _action_to_order_action


class TestParseFloat:
    def test_valid(self):
        assert _parse_float("123.45") == 123.45

    def test_comma(self):
        assert _parse_float("1,234.56") == 1234.56

    def test_empty(self):
        assert _parse_float("") is None

    def test_dash(self):
        assert _parse_float("--") is None

    def test_na(self):
        assert _parse_float("N/A") is None

    def test_none(self):
        assert _parse_float(None) is None

    def test_negative(self):
        assert _parse_float("-5.25") == -5.25


class TestParseInt:
    def test_valid(self):
        assert _parse_int("100") == 100

    def test_comma(self):
        assert _parse_int("1,000") == 1000

    def test_empty(self):
        assert _parse_int("") is None

    def test_dash(self):
        assert _parse_int("--") is None

    def test_none(self):
        assert _parse_int(None) is None


class TestGetPutCall:
    def test_put(self):
        assert _get_put_call("SPXW260330P6350") == "P"

    def test_call(self):
        assert _get_put_call("SPXW260330C6390") == "C"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            _get_put_call("INVALID")

    def test_short_symbol(self):
        assert _get_put_call("SPY260330P400") == "P"


class TestActionToOrderAction:
    def test_buy_put(self):
        assert _action_to_order_action("BO", "SPXW260330P6350") == "BP"

    def test_sell_put(self):
        assert _action_to_order_action("SO", "SPXW260330P6355") == "SP"

    def test_sell_call(self):
        assert _action_to_order_action("SO", "SPXW260330C6385") == "SC"

    def test_buy_call(self):
        assert _action_to_order_action("BO", "SPXW260330C6390") == "BC"
