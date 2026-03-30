"""Tests for options order placement, close, replace, and status."""

import json
import re
from unittest.mock import MagicMock, call
from fidelity.api_client import FidelityAPIClient, OptionLeg, AccountInfo
from tests.conftest import (
    SAMPLE_CSRF, SAMPLE_VERIFY, SAMPLE_CONFIRM, SAMPLE_ORDER_STATUS,
    SAMPLE_NET_DEBIT_CREDIT, SAMPLE_MAX_GAIN_LOSS, SAMPLE_TRADE_QUOTES,
)


def _mock_csrf_and_post(client, post_return):
    """Set up mocks for CSRF GET + a single POST."""
    csrf_resp = MagicMock()
    csrf_resp.json.return_value = SAMPLE_CSRF
    client.session.get = MagicMock(return_value=csrf_resp)
    post_resp = MagicMock()
    post_resp.json.return_value = post_return
    client.session.post = MagicMock(return_value=post_resp)
    return post_resp


class TestBuildOrderPayload:
    def test_basic_payload(self, client, ic_legs):
        body = client._build_order_payload(
            legs=ic_legs, limit_price=1.50, strategy_type="CD",
            debit_credit="CR", time_in_force="D", req_type_code="N",
        )
        od = body["orderDetails"]
        assert od["acctNum"] == "Z12345678"
        assert od["acctTypeCode"] == "M"
        assert od["netAmount"] == "1.50"
        assert od["numOfLegs"] == "4"
        assert od["dbCrEvenCode"] == "CR"
        assert od["strategyType"] == "CD"
        assert od["reqTypeCode"] == "N"

    def test_leg_structure(self, client, ic_legs):
        body = client._build_order_payload(
            legs=ic_legs, limit_price=1.50, strategy_type="CD",
            debit_credit="CR", time_in_force="D", req_type_code="N",
        )
        od = body["orderDetails"]
        assert od["leg1"] == {"action": "BO", "type": "O", "qty": 1, "symbol": "SPXW260330P6350"}
        assert od["leg2"] == {"action": "SO", "type": "O", "qty": 1, "symbol": "SPXW260330P6355"}
        assert od["leg3"] == {"action": "SO", "type": "O", "qty": 1, "symbol": "SPXW260330C6385"}
        assert od["leg4"] == {"action": "BO", "type": "O", "qty": 1, "symbol": "SPXW260330C6390"}

    def test_confirm_adds_confnum(self, client, ic_legs):
        body = client._build_order_payload(
            legs=ic_legs, limit_price=1.50, strategy_type="CD",
            debit_credit="CR", time_in_force="D", req_type_code="P",
            conf_num="C30TEST1",
        )
        assert body["orderDetails"]["confNum"] == "C30TEST1"
        assert body["orderDetails"]["reqTypeCode"] == "P"

    def test_replace_adds_order_num_orig(self, client, ic_legs):
        body = client._build_order_payload(
            legs=ic_legs, limit_price=1.50, strategy_type="CD",
            debit_credit="CR", time_in_force="D", req_type_code="N",
            original_order_id="C30ORIG1",
        )
        assert body["orderDetails"]["orderNumOrig"] == "C30ORIG1"

    def test_replace_stringifies_qty(self, client, ic_legs):
        body = client._build_order_payload(
            legs=ic_legs, limit_price=1.50, strategy_type="CD",
            debit_credit="CR", time_in_force="D", req_type_code="N",
            original_order_id="C30ORIG1",
        )
        assert body["orderDetails"]["leg1"]["qty"] == "1"  # string, not int

    def test_new_order_int_qty(self, client, ic_legs):
        body = client._build_order_payload(
            legs=ic_legs, limit_price=1.50, strategy_type="CD",
            debit_credit="CR", time_in_force="D", req_type_code="N",
        )
        assert body["orderDetails"]["leg1"]["qty"] == 1  # int

    def test_cash_account_type(self, client, ic_legs):
        client._account_info.is_margin = False
        body = client._build_order_payload(
            legs=ic_legs, limit_price=1.50, strategy_type="CD",
            debit_credit="CR", time_in_force="D", req_type_code="N",
        )
        assert body["orderDetails"]["acctTypeCode"] == "C"


class TestPreviewOptionOrder:
    def test_calls_mlo_verify(self, client, ic_legs):
        _mock_csrf_and_post(client, SAMPLE_VERIFY)

        result = client.preview_option_order(ic_legs, limit_price=1.50)
        assert "verifyDetails" in result
        assert result["verifyDetails"]["orderConfirmDetail"]["confNum"] == "C30TEST1"

        # Verify POST was called with correct URL
        post_call = client.session.post.call_args
        assert "mlo-verify" in post_call[0][0]


class TestPlaceOptionOrder:
    def test_dry_run_returns_preview(self, client, ic_legs):
        _mock_csrf_and_post(client, SAMPLE_VERIFY)

        result = client.place_option_order(ic_legs, limit_price=1.50, dry_run=True)
        assert "verifyDetails" in result
        # Only one POST (verify), no confirm
        assert client.session.post.call_count == 1

    def test_live_calls_verify_then_confirm(self, client, ic_legs):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        client.session.get = MagicMock(return_value=csrf_resp)

        verify_resp = MagicMock()
        verify_resp.json.return_value = SAMPLE_VERIFY
        confirm_resp = MagicMock()
        confirm_resp.json.return_value = SAMPLE_CONFIRM

        client.session.post = MagicMock(side_effect=[verify_resp, confirm_resp])

        result = client.place_option_order(ic_legs, limit_price=1.50, dry_run=False)
        assert "confirmDetails" in result
        assert client.session.post.call_count == 2

        # Second call should be mlo-confirm with confNum
        confirm_call = client.session.post.call_args_list[1]
        body = confirm_call.kwargs.get("json", confirm_call[1].get("json", {}))
        assert body["orderDetails"]["confNum"] == "C30TEST1"
        assert body["orderDetails"]["reqTypeCode"] == "P"

    def test_raises_on_error_messages(self, client, ic_legs):
        error_verify = {
            "verifyDetails": {"orderConfirmDetail": {"confNum": "X"}},
            "messages": [{"type": "error", "detail": "Insufficient buying power"}],
        }
        _mock_csrf_and_post(client, error_verify)

        import pytest
        with pytest.raises(ValueError, match="Insufficient buying power"):
            client.place_option_order(ic_legs, limit_price=1.50, dry_run=False)


class TestCloseOptionOrder:
    def test_reverses_legs(self, client, ic_legs):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        client.session.get = MagicMock(return_value=csrf_resp)

        verify_resp = MagicMock()
        verify_resp.json.return_value = SAMPLE_VERIFY
        client.session.post = MagicMock(return_value=verify_resp)

        client.close_option_order(ic_legs, limit_price=1.50, dry_run=True)

        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        od = body["orderDetails"]

        # Strategy should be CU (close/unwind)
        assert od["strategyType"] == "CU"
        assert od["dbCrEvenCode"] == "DB"

        # All legs should have type "C"
        for i in range(1, 5):
            assert od[f"leg{i}"]["type"] == "C"

        # Actions should be reversed: BO->SO, SO->BO
        actions = [od[f"leg{i}"]["action"] for i in range(1, 5)]
        assert "SO" in actions
        assert "BO" in actions

    def test_sorts_by_descending_strike(self, client, ic_legs):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        client.session.get = MagicMock(return_value=csrf_resp)

        verify_resp = MagicMock()
        verify_resp.json.return_value = SAMPLE_VERIFY
        client.session.post = MagicMock(return_value=verify_resp)

        client.close_option_order(ic_legs, limit_price=1.50, dry_run=True)

        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        od = body["orderDetails"]

        # Strikes should descend: 6390, 6385, 6355, 6350
        strikes = []
        for i in range(1, 5):
            sym = od[f"leg{i}"]["symbol"]
            match = re.search(r'[PC](\d+)$', sym)
            strikes.append(int(match.group(1)))
        assert strikes == sorted(strikes, reverse=True)


class TestReplaceOptionOrder:
    def test_dry_run_returns_preview(self, client, ic_legs):
        _mock_csrf_and_post(client, SAMPLE_VERIFY)

        result = client.replace_option_order(
            "C30ORIG1", ic_legs, limit_price=1.20, dry_run=True,
        )
        assert "verifyDetails" in result

        # Check orderNumOrig was included
        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        assert body["orderDetails"]["orderNumOrig"] == "C30ORIG1"


class TestGetOrderStatus:
    def test_returns_order_details(self, client):
        _mock_csrf_and_post(client, SAMPLE_ORDER_STATUS)

        result = client.get_order_status("C30TEST1")
        assert result["orderDetails"][0]["decodeStatus"] == "FILLED"
        assert result["orderDetails"][0]["strategyName"] == "Condor"


class TestGetNetDebitCredit:
    def test_fetches_quotes_and_calculates(self, client, ic_legs):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        client.session.get = MagicMock(return_value=csrf_resp)

        quotes_resp = MagicMock()
        quotes_resp.json.return_value = SAMPLE_TRADE_QUOTES
        ndc_resp = MagicMock()
        ndc_resp.json.return_value = SAMPLE_NET_DEBIT_CREDIT

        client.session.post = MagicMock(side_effect=[quotes_resp, ndc_resp])

        result = client.get_net_debit_credit(ic_legs, limit_price=1.50)
        assert result["netBid"] == 1.5
        assert result["mid"] == 1.65
        assert result["netDebitOrCredit"] == "Net Credit"

    def test_sends_dash_prefixed_symbols(self, client, ic_legs):
        csrf_resp = MagicMock()
        csrf_resp.json.return_value = SAMPLE_CSRF
        client.session.get = MagicMock(return_value=csrf_resp)

        quotes_resp = MagicMock()
        quotes_resp.json.return_value = SAMPLE_TRADE_QUOTES
        ndc_resp = MagicMock()
        ndc_resp.json.return_value = SAMPLE_NET_DEBIT_CREDIT

        client.session.post = MagicMock(side_effect=[quotes_resp, ndc_resp])

        client.get_net_debit_credit(ic_legs, limit_price=1.50)

        # First POST is quotes — check symbols have dash prefix
        quotes_call = client.session.post.call_args_list[0]
        body = quotes_call.kwargs.get("json", quotes_call[1].get("json", {}))
        for sym in body["symbols"]:
            assert sym.startswith("-")


class TestGetMaxGainLoss:
    def test_returns_gain_loss(self, client, ic_legs):
        _mock_csrf_and_post(client, SAMPLE_MAX_GAIN_LOSS)

        result = client.get_max_gain_loss(ic_legs, limit_price=1.50)
        assert result["maxGain"] == "$150.00"
        assert result["maxLoss"] == "-$350.00"
        assert "breakEvenPoint" in result

    def test_uses_long_form_action_codes(self, client, ic_legs):
        _mock_csrf_and_post(client, SAMPLE_MAX_GAIN_LOSS)

        client.get_max_gain_loss(ic_legs, limit_price=1.50)

        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        codes = [leg["orderActionCode"] for leg in body["legDetails"]]
        assert "BOPEN" in codes
        assert "SOPEN" in codes

    def test_sells_have_negative_qty(self, client, ic_legs):
        _mock_csrf_and_post(client, SAMPLE_MAX_GAIN_LOSS)

        client.get_max_gain_loss(ic_legs, limit_price=1.50)

        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        for leg in body["legDetails"]:
            if leg["orderActionCode"] == "SOPEN":
                assert leg["qty"] < 0
            else:
                assert leg["qty"] > 0
