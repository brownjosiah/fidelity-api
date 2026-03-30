"""Tests for equity order placement and cancel."""

import json
from unittest.mock import MagicMock
from fidelity.api_client import FidelityAPIClient
from tests.conftest import (
    SAMPLE_EQUITY_PREVIEW, SAMPLE_EQUITY_PLACE,
    SAMPLE_CANCEL_PREVIEW, SAMPLE_CANCEL_PLACE,
)


def _mock_equity_post(client, response_text):
    """Mock a POST that returns text/html with JSON body (equity pattern)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = response_text
    mock_resp.json.return_value = json.loads(response_text)
    mock_resp.raise_for_status = MagicMock()

    # Equity needs CSRF from cookie, mock the cookie
    cookie_mock = MagicMock()
    cookie_mock.name = "_brkg.ap122489.equitytradeticket.csrf"
    cookie_mock.value = "testcsrfsecret123456789012"
    client.session.cookies = MagicMock()
    client.session.cookies.__iter__ = MagicMock(return_value=iter([cookie_mock]))
    client.session.post = MagicMock(return_value=mock_resp)
    return mock_resp


class TestPreviewEquityOrder:
    def test_preview_market_buy(self, client):
        _mock_equity_post(client, SAMPLE_EQUITY_PREVIEW)

        result = client.preview_equity_order("QS", "B", 1, price_type="M")
        preview = result["preview"]
        assert preview["orderConfirmDetail"]["confNum"] == "249TEST1"
        assert preview["orderConfirmDetail"]["priceDetail"]["price"] == 6.07

    def test_preview_limit_buy(self, client):
        _mock_equity_post(client, SAMPLE_EQUITY_PREVIEW)

        result = client.preview_equity_order("QS", "B", 1, price_type="L", limit_price=5.00)

        # Check the request body
        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        assert body["orderDetails"]["priceTypeCode"] == "L"
        assert body["orderDetails"]["limitPrice"] == 5.00

    def test_preview_sell(self, client):
        _mock_equity_post(client, SAMPLE_EQUITY_PREVIEW)

        result = client.preview_equity_order("QS", "S", 1, price_type="M")

        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        assert body["orderDetails"]["orderAction"] == "S"
        assert body["orderDetails"]["orderActionCode"] == "S"

    def test_sends_equity_csrf(self, client):
        _mock_equity_post(client, SAMPLE_EQUITY_PREVIEW)

        client.preview_equity_order("QS", "B", 1)

        post_call = client.session.post.call_args
        headers = post_call.kwargs.get("headers", post_call[1].get("headers", {}))
        # CSRF token should be salt-hash format
        token = headers.get("X-CSRF-TOKEN", "")
        assert "-" in token
        assert len(token) > 10


class TestPlaceEquityOrder:
    def test_dry_run_returns_preview(self, client):
        _mock_equity_post(client, SAMPLE_EQUITY_PREVIEW)

        result = client.place_equity_order("QS", "B", 1, dry_run=True)
        assert "preview" in result
        assert client.session.post.call_count == 1

    def test_live_calls_preview_then_place(self, client):
        preview_resp = MagicMock()
        preview_resp.json.return_value = json.loads(SAMPLE_EQUITY_PREVIEW)
        preview_resp.raise_for_status = MagicMock()

        place_resp = MagicMock()
        place_resp.json.return_value = json.loads(SAMPLE_EQUITY_PLACE)
        place_resp.raise_for_status = MagicMock()

        cookie_mock = MagicMock()
        cookie_mock.name = "_brkg.ap122489.equitytradeticket.csrf"
        cookie_mock.value = "testcsrfsecret123456789012"
        client.session.cookies = MagicMock()
        client.session.cookies.__iter__ = MagicMock(return_value=iter([cookie_mock, cookie_mock]))
        client.session.post = MagicMock(side_effect=[preview_resp, place_resp])

        result = client.place_equity_order("QS", "B", 1, price_type="M", dry_run=False)
        assert "place" in result
        assert client.session.post.call_count == 2

        # Second call should include confNum
        place_call = client.session.post.call_args_list[1]
        body = place_call.kwargs.get("json", place_call[1].get("json", {}))
        assert body["orderDetails"]["confNum"] == "249TEST1"


class TestCancelOrder:
    def test_cancel_preview(self, client):
        _mock_equity_post(client, SAMPLE_CANCEL_PREVIEW)

        result = client.cancel_order("249TEST1", dry_run=True)
        preview = result["preview"]
        details = preview["cancelConfirmDetail"][0]
        assert details["confNum"] == "249TEST1"
        assert details["remainingQty"] == 1

    def test_cancel_execute(self, client):
        preview_resp = MagicMock()
        preview_resp.json.return_value = json.loads(SAMPLE_CANCEL_PREVIEW)
        preview_resp.raise_for_status = MagicMock()

        place_resp = MagicMock()
        place_resp.json.return_value = json.loads(SAMPLE_CANCEL_PLACE)
        place_resp.raise_for_status = MagicMock()

        cookie_mock = MagicMock()
        cookie_mock.name = "_brkg.ap122489.equitytradeticket.csrf"
        cookie_mock.value = "testcsrfsecret123456789012"
        client.session.cookies = MagicMock()
        client.session.cookies.__iter__ = MagicMock(return_value=iter([cookie_mock, cookie_mock]))
        client.session.post = MagicMock(side_effect=[preview_resp, place_resp])

        result = client.cancel_order("249TEST1", dry_run=False)
        assert "place" in result
        assert client.session.post.call_count == 2

    def test_cancel_sends_correct_payload(self, client):
        _mock_equity_post(client, SAMPLE_CANCEL_PREVIEW)

        client.cancel_order("249TEST1", dry_run=True)

        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        assert body["cancelOrderDetails"]["confNum"] == "249TEST1"
        assert body["cancelOrderDetails"]["acctNum"] == "Z12345678"
