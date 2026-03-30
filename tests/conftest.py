"""Shared fixtures for fidelity API tests."""

import json
import pytest
from unittest.mock import MagicMock, patch
from fidelity.api_client import FidelityAPIClient, AccountInfo, OptionLeg


# --- Sample response data (derived from live captures) ---

SAMPLE_QUOTE_RESPONSE = {
    "responseTime": 26.5,
    "userInfo": {"realtimeEligible": "Y"},
    "preferredQuoteData": [],
    "quoteResponse": [
        {
            "status": {"errorCode": 0, "errorText": ""},
            "requestSymbol": ".SPX",
            "quoteData": {
                "companyName": "S&P 500 INDEX",
                "symbol": ".SPX",
                "lastPrice": "6375.00",
                "dayHigh": "6400.00",
                "dayLow": "6350.00",
                "volume": "2100000000",
                "netChgToday": "-50.00",
                "pctChgToday": "-0.78",
                "prevClosePrice": "6425.00",
                "openPrice": "6390.00",
                "bidPrice": "6374.50",
                "askPrice": "6375.50",
            },
        }
    ],
}

SAMPLE_MULTI_QUOTE_RESPONSE = {
    "quoteResponse": [
        {
            "status": {"errorCode": 0},
            "requestSymbol": ".SPX",
            "quoteData": {"lastPrice": "6375.00", "symbol": ".SPX"},
        },
        {
            "status": {"errorCode": 0},
            "requestSymbol": ".VIX",
            "quoteData": {"lastPrice": "25.50", "symbol": ".VIX"},
        },
    ],
}

SAMPLE_EXPIRATIONS = {
    "expirations": [
        {"date": "2026-03-30", "optionPeriodicity": "W", "setType": "P", "key": "2026-03-30P"},
        {"date": "2026-03-31", "optionPeriodicity": "Q", "setType": "P", "key": "2026-03-31P"},
        {"date": "2026-04-06", "optionPeriodicity": "W", "setType": "P", "key": "2026-04-06P"},
    ],
}

SAMPLE_CHAIN = {
    "callsAndPuts": [
        {
            "expirationData": {"date": "03/30/2026", "daysToExpiration": "0", "settlementType": "PM"},
            "strike": "6370.00",
            "callBid": "13.50", "callAsk": "13.70", "callBidSize": "10", "callAskSize": "15",
            "callDelta": "0.508", "callGamma": "0.005", "callTheta": "-2.10", "callVega": "1.50",
            "callRho": "0.30", "callImpliedVolatility": "0.26", "callVolume": "5000",
            "callOpenInterest": "12000", "callSelection": "-SPXW260330C6370",
            "callLast": "13.60", "callChange": "-1.20", "callTimeValue": "13.60",
            "callIntrinsicValue": "0.00",
            "putBid": "12.80", "putAsk": "13.00", "putBidSize": "8", "putAskSize": "12",
            "putDelta": "-0.492", "putGamma": "0.005", "putTheta": "-2.05", "putVega": "1.48",
            "putRho": "-0.28", "putImpliedVolatility": "0.27", "putVolume": "4500",
            "putOpenInterest": "11000", "putSelection": "-SPXW260330P6370",
            "putLast": "12.90", "putChange": "1.10",
            "adj": "",
        },
        {
            "expirationData": {"date": "03/30/2026", "daysToExpiration": "0", "settlementType": "PM"},
            "strike": "6375.00",
            "callBid": "10.20", "callAsk": "10.40", "callBidSize": "12", "callAskSize": "18",
            "callDelta": "0.45", "callGamma": "0.004", "callTheta": "-1.90", "callVega": "1.40",
            "callRho": "0.25", "callImpliedVolatility": "0.25", "callVolume": "3000",
            "callOpenInterest": "8000", "callSelection": "-SPXW260330C6375",
            "callLast": "10.30",
            "putBid": "15.50", "putAsk": "15.70", "putBidSize": "6", "putAskSize": "10",
            "putDelta": "-0.55", "putGamma": "0.004", "putTheta": "-1.95", "putVega": "1.42",
            "putRho": "-0.32", "putImpliedVolatility": "0.28", "putVolume": "4000",
            "putOpenInterest": "9000", "putSelection": "-SPXW260330P6375",
            "putLast": "15.60",
            "adj": "",
        },
    ],
    "underlyingSymbol": ".SPX",
    "adjustedOptionsData": [],
}

SAMPLE_CSRF = {"csrfToken": "test-csrf-token-12345"}

SAMPLE_ACCOUNTS = [
    {
        "acctNum": "Z12345678",
        "isDefaultAcct": False,
        "accountDetails": {
            "acctType": "Brokerage",
            "acctSubType": "Brokerage",
            "acctSubTypeDesc": "Brokerage General Investing",
            "name": "Individual - TOD",
            "regTypeDesc": "Individual - TOD",
            "relTypeCode": "INDIVIDUAL",
            "hiddenInd": False,
            "isAdvisorAcct": False,
            "isAuthorizedAcct": False,
            "isRetirement": False,
        },
        "optionLevel": 5,
        "isMarginEstb": True,
        "isOptionEstb": True,
        "accountFeatures": {},
    },
    {
        "acctNum": "Z87654321",
        "isDefaultAcct": False,
        "accountDetails": {
            "acctType": "IRA",
            "acctSubType": "IRA",
            "acctSubTypeDesc": "Roth IRA",
            "name": "Roth IRA",
            "regTypeDesc": "Roth IRA",
            "isRetirement": True,
        },
        "optionLevel": 0,
        "isMarginEstb": False,
        "isOptionEstb": False,
        "accountFeatures": {},
    },
]

SAMPLE_VERIFY = {
    "verifyDetails": {
        "acctNum": "Z12345678",
        "orderConfirmDetail": {
            "confNum": "C30TEST1",
            "acctTypeCode": "M",
            "strategy": "Condor",
            "orderDetail": {
                "netValues": {
                    "netBid": {"value": 1.5},
                    "netAsk": {"value": 1.8},
                    "netMid": {"value": 1.65},
                    "totalCost": 147.4,
                    "netCommission": 2.6,
                },
            },
        },
        "tifCode": "D",
        "dbCrEvenCode": "CR",
    },
    "messages": [
        {"message": "Other", "detail": "PM settled warning", "type": "warning", "code": "1999"},
    ],
}

SAMPLE_CONFIRM = {
    "confirmDetails": {
        "acctNum": "Z12345678",
        "orderConfirmDetail": {
            "confNum": "C30TEST1",
            "strategy": "Condor",
            "netAmount": 1.5,
        },
    },
    "messages": [],
}

SAMPLE_ORDER_STATUS = {
    "orderDetails": [
        {
            "statusCode": "5",
            "statusDesc": "Filled",
            "decodeStatus": "FILLED",
            "cancelableInd": False,
            "replaceableInd": False,
            "strategyName": "Condor",
            "limitPrice": 1.5,
            "priceTypeCode": "L",
            "dbCrEvenCode": "CR",
            "tifCode": "D",
            "orderLegInfoDetail": {
                "orderActionCode": "BP",
                "qty": 1,
                "callPut": "Put",
                "symbol": "SPXW260330P6350",
                "strikePrice": 6350,
                "expirationDate": "Mar 30, 2026",
            },
        }
    ],
}

SAMPLE_NET_DEBIT_CREDIT = {
    "acctNum": "Z12345678",
    "estComm": 2.6,
    "totalCost": "147.40",
    "gcd": 1,
    "netBid": 1.5,
    "netAsk": 1.8,
    "mid": 1.65,
    "netDebitOrCredit": "Net Credit",
}

SAMPLE_MAX_GAIN_LOSS = {
    "maxLossNumber": "-350",
    "maxGainNumber": "150",
    "breakEvenPoint": ".SPX at $6,353.50 and $6,386.50",
    "maxLoss": "-$350.00",
    "maxGain": "$150.00",
    "containsCloseAction": False,
}

SAMPLE_TRADE_QUOTES = {
    "quotes": [
        {"symbol": "-SPXW260330P6350", "bid": 8.0, "ask": 8.2},
        {"symbol": "-SPXW260330P6355", "bid": 10.5, "ask": 10.7},
        {"symbol": "-SPXW260330C6385", "bid": 7.5, "ask": 7.7},
        {"symbol": "-SPXW260330C6390", "bid": 5.8, "ask": 6.0},
    ],
}

SAMPLE_EQUITY_PREVIEW = (
    '{"preview":{"acctNum":"Z12345678","orderConfirmDetail":'
    '{"respTypeCode":"V","confNum":"249TEST1","acctNum":"Z12345678",'
    '"acctTypeCode":"M","priceDetail":{"price":6.07,"bidPrice":6.06,"askPrice":6.08},'
    '"estCommissionDetail":{"estCommission":0},"netAmount":6.07}}}'
)

SAMPLE_EQUITY_PLACE = (
    '{"place":{"acctNum":"Z12345678","orderConfirmDetail":'
    '{"respTypeCode":"A","confNum":"249TEST1","acctNum":"Z12345678",'
    '"acctTypeCode":"M","priceDetail":{"price":6.07},'
    '"estCommissionDetail":{"estCommission":0},"netAmount":6.07}}}'
)

SAMPLE_CANCEL_PREVIEW = (
    '{"preview":{"cancelConfirmDetail":[{"respTypeCode":"V","confNum":"249TEST1",'
    '"acctNum":"Z12345678","origQty":1,"execQty":0,"remainingQty":1}]}}'
)

SAMPLE_CANCEL_PLACE = (
    '{"place":{"cancelConfirmDetail":[{"respTypeCode":"A","confNum":"249TEST1",'
    '"acctNum":"Z12345678","origQty":1,"execQty":0,"remainingQty":1}]}}'
)

SAMPLE_ORDER_HISTORY = {
    "data": {
        "getTransactions": {
            "orders": [
                {
                    "acctNum": "Z12345678",
                    "description": "Buy 1 Shares of QS at Market (Day)",
                    "date": "30 Mar 2026",
                    "status": "Filled at $6.07",
                    "confNumOrig": "249TEST1",
                    "cancelableInd": "false",
                    "replaceableInd": "false",
                    "isOption": False,
                },
                {
                    "acctNum": "Z12345678",
                    "description": "Buy to Open 1 Contract SPXW",
                    "date": "30 Mar 2026",
                    "status": "Filled at $2.96",
                    "confNumOrig": "C30TEST1",
                    "cancelableInd": "false",
                    "replaceableInd": "false",
                    "isOption": True,
                },
            ],
        }
    }
}

SAMPLE_BALANCES = {
    "acctNum": "Z12345678",
    "totalAcctVal": "156000.00",
    "cashAvailForTrade": "50000.00",
    "intraDayBP": "100000.00",
    "mrgnBP": "100000.00",
    "nonMrgnBP": "50000.00",
    "isMrgnAcct": True,
}

SAMPLE_POSITIONS = {
    "positionDetails": [
        {"securityType": "Option", "symbol": "SPXW260330P6350", "intradayTradeDateShares": "1"},
        {"securityType": "Equity", "symbol": "QS", "intradayTradeDateShares": "10"},
    ],
    "hasOwnedOptionPosition": True,
    "hasOwnedEquityETFPosition": True,
}


# --- Fixtures ---

@pytest.fixture
def mock_session():
    """Create a mock requests.Session."""
    session = MagicMock()
    session.cookies = MagicMock()
    session.cookies.items = MagicMock(return_value=[])
    return session


@pytest.fixture
def client():
    """Create a FidelityAPIClient with fake cookies and a pre-set account."""
    c = FidelityAPIClient(cookies={"FC": "fake", "SC": "fake"})
    c._accounts = [
        AccountInfo(
            acct_num="Z12345678",
            acct_type="Brokerage",
            acct_sub_type="Brokerage",
            acct_sub_type_desc="Brokerage General Investing",
            name="Individual - TOD",
            reg_type_desc="Individual - TOD",
            option_level=5,
            is_margin=True,
            is_option=True,
            is_retirement=False,
        ),
    ]
    c._account_info = c._accounts[0]
    return c


@pytest.fixture
def ic_legs():
    """Standard iron condor legs for testing."""
    return [
        OptionLeg(symbol="SPXW260330P6350", action="BO", quantity=1),
        OptionLeg(symbol="SPXW260330P6355", action="SO", quantity=1),
        OptionLeg(symbol="SPXW260330C6385", action="SO", quantity=1),
        OptionLeg(symbol="SPXW260330C6390", action="BO", quantity=1),
    ]
