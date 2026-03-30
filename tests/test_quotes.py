"""Tests for quote and market data methods."""

from unittest.mock import MagicMock, patch
from tests.conftest import SAMPLE_QUOTE_RESPONSE, SAMPLE_MULTI_QUOTE_RESPONSE


class TestGetQuote:
    def test_returns_quote_data(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_QUOTE_RESPONSE
        client.session.get = MagicMock(return_value=mock_resp)

        result = client.get_quote(".SPX")
        assert result["lastPrice"] == "6375.00"
        assert result["symbol"] == ".SPX"

    def test_handles_old_status_format(self, client):
        """Status as string "0" (old format)."""
        resp_data = {
            "quoteResponse": [
                {"status": "0", "requestSymbol": ".SPX", "quoteData": {"lastPrice": "6375.00"}},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = resp_data
        client.session.get = MagicMock(return_value=mock_resp)

        result = client.get_quote(".SPX")
        assert result["lastPrice"] == "6375.00"

    def test_handles_new_status_format(self, client):
        """Status as dict {"errorCode": 0} (new format)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_QUOTE_RESPONSE
        client.session.get = MagicMock(return_value=mock_resp)

        result = client.get_quote(".SPX")
        assert result["lastPrice"] == "6375.00"

    def test_returns_empty_on_error(self, client):
        resp_data = {
            "quoteResponse": [
                {"status": {"errorCode": 1, "errorText": "not found"}, "requestSymbol": "FAKE"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = resp_data
        client.session.get = MagicMock(return_value=mock_resp)

        result = client.get_quote("FAKE")
        assert result == {}


class TestGetQuotes:
    def test_returns_multiple(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_MULTI_QUOTE_RESPONSE
        client.session.get = MagicMock(return_value=mock_resp)

        result = client.get_quotes([".SPX", ".VIX"])
        assert ".SPX" in result
        assert ".VIX" in result
        assert result[".SPX"]["lastPrice"] == "6375.00"
        assert result[".VIX"]["lastPrice"] == "25.50"


class TestSpxVixPrice:
    def test_get_spx_price(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_QUOTE_RESPONSE
        client.session.get = MagicMock(return_value=mock_resp)

        price = client.get_spx_price()
        assert price == 6375.0

    def test_get_spx_price_none(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"quoteResponse": []}
        client.session.get = MagicMock(return_value=mock_resp)

        assert client.get_spx_price() is None

    def test_get_vix_price(self, client):
        vix_resp = {
            "quoteResponse": [
                {"status": {"errorCode": 0}, "requestSymbol": ".VIX", "quoteData": {"lastPrice": "25.50"}},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = vix_resp
        client.session.get = MagicMock(return_value=mock_resp)

        assert client.get_vix_price() == 25.5
