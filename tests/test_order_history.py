"""Tests for order history."""

from unittest.mock import MagicMock
from tests.conftest import SAMPLE_ORDER_HISTORY


class TestGetOrderHistory:
    def test_returns_orders(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_ORDER_HISTORY
        client.session.post = MagicMock(return_value=mock_resp)

        orders = client.get_order_history(days=7)
        assert len(orders) == 2
        assert orders[0]["confNumOrig"] == "249TEST1"
        assert orders[1]["confNumOrig"] == "C30TEST1"

    def test_sends_correct_graphql_query(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_ORDER_HISTORY
        client.session.post = MagicMock(return_value=mock_resp)

        client.get_order_history(days=30)

        post_call = client.session.post.call_args
        body = post_call.kwargs.get("json", post_call[1].get("json", {}))
        assert body["operationName"] == "getTransactions"
        assert body["variables"]["acctIdList"] == "Z12345678"
        assert body["variables"]["searchCriteriaDetail"]["timePeriod"] == 30

    def test_empty_orders(self, client):
        empty_resp = {"data": {"getTransactions": {"orders": []}}}
        mock_resp = MagicMock()
        mock_resp.json.return_value = empty_resp
        client.session.post = MagicMock(return_value=mock_resp)

        orders = client.get_order_history()
        assert orders == []
