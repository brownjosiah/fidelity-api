"""Tests for account discovery and management."""

from unittest.mock import MagicMock
from fidelity.api_client import FidelityAPIClient, AccountInfo
from tests.conftest import SAMPLE_ACCOUNTS, SAMPLE_CSRF, SAMPLE_BALANCES, SAMPLE_POSITIONS


class TestDiscoverAccounts:
    def test_discovers_accounts(self, client):
        # Clear pre-set accounts so discover_accounts actually runs
        client._accounts = []
        client._account_info = None

        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF

        acct_resp = MagicMock()
        acct_resp.json.return_value = SAMPLE_ACCOUNTS

        client.session.get = MagicMock(return_value=csrf_resp)
        client.session.post = MagicMock(return_value=acct_resp)

        accounts = client.discover_accounts()
        assert len(accounts) == 2
        assert accounts[0].acct_num == "Z12345678"
        assert accounts[0].is_margin is True
        assert accounts[0].option_level == 5
        assert accounts[1].acct_num == "Z87654321"
        assert accounts[1].is_retirement is True

    def test_selects_options_margin_as_default(self, client):
        client._accounts = []
        client._account_info = None

        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        acct_resp = MagicMock()
        acct_resp.json.return_value = SAMPLE_ACCOUNTS

        client.session.get = MagicMock(return_value=csrf_resp)
        client.session.post = MagicMock(return_value=acct_resp)

        client.discover_accounts()
        assert client._account_info.acct_num == "Z12345678"
        assert client._account_info.is_option is True

    def test_caches_results(self, client):
        # client fixture already has accounts set
        client.session.post = MagicMock()
        accounts = client.discover_accounts()
        assert len(accounts) == 1
        # session.post should not be called since cached
        client.session.post.assert_not_called()


class TestGetAccount:
    def test_default_account(self, client):
        acct = client.get_account()
        assert acct.acct_num == "Z12345678"

    def test_specific_account(self, client):
        acct = client.get_account("Z12345678")
        assert acct.acct_num == "Z12345678"

    def test_not_found_raises(self, client):
        import pytest
        with pytest.raises(ValueError, match="not found"):
            client.get_account("ZNOTEXIST")


class TestGetBalances:
    def test_returns_balances(self, client):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        bal_resp = MagicMock()
        bal_resp.json.return_value = SAMPLE_BALANCES

        client.session.get = MagicMock(return_value=csrf_resp)
        client.session.post = MagicMock(return_value=bal_resp)

        result = client.get_balances()
        assert result["totalAcctVal"] == "156000.00"
        assert result["isMrgnAcct"] is True


class TestGetPositions:
    def test_returns_positions(self, client):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        pos_resp = MagicMock()
        pos_resp.json.return_value = SAMPLE_POSITIONS

        client.session.get = MagicMock(return_value=csrf_resp)
        client.session.post = MagicMock(return_value=pos_resp)

        result = client.get_positions()
        assert len(result["positionDetails"]) == 2

    def test_get_option_positions_filters(self, client):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        pos_resp = MagicMock()
        pos_resp.json.return_value = SAMPLE_POSITIONS

        client.session.get = MagicMock(return_value=csrf_resp)
        client.session.post = MagicMock(return_value=pos_resp)

        options = client.get_option_positions()
        assert len(options) == 1
        assert options[0]["securityType"] == "Option"
