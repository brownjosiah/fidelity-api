"""Tests for option chain and expiration methods."""

from unittest.mock import MagicMock, call
from tests.conftest import SAMPLE_EXPIRATIONS, SAMPLE_CHAIN


class TestGetOptionExpirations:
    def test_returns_expirations(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_EXPIRATIONS
        client.session.get = MagicMock(return_value=mock_resp)

        exps = client.get_option_expirations(".SPX")
        assert len(exps) == 3
        assert exps[0]["date"] == "2026-03-30"
        assert exps[0]["optionPeriodicity"] == "W"


class TestGet0dteExpiration:
    def test_returns_first_expiration(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_EXPIRATIONS
        client.session.get = MagicMock(return_value=mock_resp)

        result = client.get_0dte_expiration(".SPX")
        assert result == "2026-03-30"

    def test_returns_none_when_empty(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"expirations": []}
        client.session.get = MagicMock(return_value=mock_resp)

        assert client.get_0dte_expiration(".SPX") is None


class TestGetOptionChain:
    def test_returns_chain_with_greeks(self, client):
        # First call: expirations, second call: chain
        exp_resp = MagicMock()
        exp_resp.json.return_value = SAMPLE_EXPIRATIONS

        chain_resp = MagicMock()
        chain_resp.json.return_value = SAMPLE_CHAIN

        client.session.get = MagicMock(side_effect=[exp_resp, chain_resp])

        chain = client.get_option_chain("SPX")
        assert len(chain) == 2
        assert chain[0]["strike"] == "6370.00"
        assert chain[0]["callBid"] == "13.50"
        assert chain[0]["callDelta"] == "0.508"
        assert chain[0]["putSelection"] == "-SPXW260330P6370"

    def test_builds_correct_settlement_types(self, client):
        exp_resp = MagicMock()
        exp_resp.json.return_value = SAMPLE_EXPIRATIONS
        chain_resp = MagicMock()
        chain_resp.json.return_value = SAMPLE_CHAIN

        client.session.get = MagicMock(side_effect=[exp_resp, chain_resp])

        client.get_option_chain("SPX")

        # Check the chain request params
        chain_call = client.session.get.call_args_list[1]
        params = chain_call.kwargs.get("params", chain_call[1].get("params", {}))
        assert params["expirationDates"] == "03/30/2026"
        assert "Mar 30 2026P|W" in params["settlementTypes"]
        assert params["symbol"] == "SPX"
        assert params["adjustedOptionsData"] == "true"

    def test_converts_yyyy_mm_dd_dates(self, client):
        exp_resp = MagicMock()
        exp_resp.json.return_value = SAMPLE_EXPIRATIONS
        chain_resp = MagicMock()
        chain_resp.json.return_value = SAMPLE_CHAIN

        client.session.get = MagicMock(side_effect=[exp_resp, chain_resp])

        # Pass YYYY-MM-DD dates explicitly
        client.get_option_chain("SPX", expiration_dates=["2026-03-30"])

        chain_call = client.session.get.call_args_list[1]
        params = chain_call.kwargs.get("params", chain_call[1].get("params", {}))
        assert params["expirationDates"] == "03/30/2026"


class TestGetOptionChainParsed:
    def test_parses_to_floats(self, client):
        exp_resp = MagicMock()
        exp_resp.json.return_value = SAMPLE_EXPIRATIONS
        chain_resp = MagicMock()
        chain_resp.json.return_value = SAMPLE_CHAIN

        client.session.get = MagicMock(side_effect=[exp_resp, chain_resp])

        parsed = client.get_option_chain_parsed("SPX")
        assert len(parsed) == 2
        row = parsed[0]
        assert row["strike"] == 6370.0
        assert row["call_bid"] == 13.5
        assert row["call_ask"] == 13.7
        assert row["call_delta"] == 0.508
        assert row["put_iv"] == 0.27

    def test_computes_mid_and_spread(self, client):
        exp_resp = MagicMock()
        exp_resp.json.return_value = SAMPLE_EXPIRATIONS
        chain_resp = MagicMock()
        chain_resp.json.return_value = SAMPLE_CHAIN

        client.session.get = MagicMock(side_effect=[exp_resp, chain_resp])

        parsed = client.get_option_chain_parsed("SPX")
        row = parsed[0]
        assert row["call_mid"] == 13.6  # (13.50 + 13.70) / 2
        assert row["call_spread"] == 0.2  # 13.70 - 13.50
