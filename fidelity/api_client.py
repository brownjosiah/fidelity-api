"""
Direct HTTP client for Fidelity's internal REST APIs.

Discovered via network interception (capture_api.py). Bypasses DOM
automation for data operations — faster, more reliable, no DOM breakage.

Auth model:
  - Login via Playwright (FidelityAutomation) to get session cookies
  - Extract cookies from Playwright's storage_state
  - Data endpoints (slo-chain, quotes): cookie-only auth
  - Trading endpoints (balances, positions): CSRF + cookie auth
  - CSRF token from GET /prgw/digital/research/api/tokens

Usage:
    from fidelity.fidelity import FidelityAutomation
    from fidelity.api_client import FidelityAPIClient

    # Login via browser
    fid = FidelityAutomation(headless=True, save_state=True)
    fid.login(username, password, totp_secret=secret)

    # Create API client from browser session
    client = FidelityAPIClient.from_automation(fid)

    # Get SPX option chain with Greeks
    chain = client.get_option_chain("SPX")
    for strike in chain:
        print(f"{strike['strike']}: call={strike['callBid']}/{strike['callAsk']} delta={strike['callDelta']}")

    # Get quotes
    spx_price = client.get_quote(".SPX")
    vix_price = client.get_quote(".VIX")

    # Get account balances (requires CSRF)
    balances = client.get_balances()
    print(f"Account value: ${balances['totalAcctVal']}")
"""

import json
import time
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, urlencode

import requests


# --- API Base URLs ---

BASE_URL = "https://digital.fidelity.com"

# Data endpoints (cookie-only)
ENDPOINTS = {
    "slo_chain": "/ftgw/digital/options-research/api/slo-chain",
    "mlo_chain": "/ftgw/digital/options-research/api/mlo-chain",
    "quotes": "/ftgw/digital/options-research/api/quotes",
    "option_expirations": "/ftgw/digital/options-research/api/option-expirations",
    "volatility_extended": "/ftgw/digital/options-research/api/volatility-extended",
    "key_statistics": "/ftgw/digital/options-research/api/key-statistics",
    "research_data": "/ftgw/digital/options-research/api/research-data",
    "account_positions_research": "/ftgw/digital/options-research/api/account-positions",
    # Trading endpoints (csrf + cookie)
    "csrf_token": "/prgw/digital/research/api/tokens",
    "trade_balances": "/ftgw/digital/trade-options/api/balances",
    "trade_positions": "/ftgw/digital/trade-options/api/positions",
    "trade_rules_engine": "/ftgw/digital/trade-options/api/rules-engine",
    "trade_config": "/ftgw/digital/trade-options/api/config",
    "trade_account_fusion": "/ftgw/digital/trade-options/api/account-fusion",
    "trade_autosuggest": "/ftgw/digital/trade-options/api/autosuggest",
    # Multi-leg order endpoints (csrf + cookie)
    "mlo_verify": "/ftgw/digital/trade-options/api/mlo-verify",
    "mlo_confirm": "/ftgw/digital/trade-options/api/mlo-confirm",
    "mlo_verify_replace": "/ftgw/digital/trade-options/api/mlo-verify-replace",
    "mlo_confirm_replace": "/ftgw/digital/trade-options/api/mlo-confirm-replace",
    "trade_orders": "/ftgw/digital/trade-options/api/orders",
    "trade_quotes": "/ftgw/digital/trade-options/api/quotes",
    "net_debit_credit": "/ftgw/digital/trade-options/api/net-debit-credit",
    "max_gain_loss": "/ftgw/digital/trade-options/api/max-gain-loss",
    # Account context
    "account_context": "/ftgw/digital/pico/api/v1/context/account",
    # Alternate quote source (traderplus)
    "traderplus_quotes": "/ftgw/digital/traderplus-api/api/quotes",
    "traderplus_positions": "/ftgw/digital/traderplus-api/api/positions",
    "traderplus_accounts": "/ftgw/digital/traderplus-api/api/accounts",
}

# Common request headers
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Origin": "https://digital.fidelity.com",
    "Referer": "https://digital.fidelity.com/ftgw/digital/options-research/",
}

# Referer header override for trade-options endpoints
TRADE_REFERER = "https://digital.fidelity.com/ftgw/digital/trade-options"

# Action code mappings
# Short-form (verify/confirm payloads) -> long-form (max-gain-loss endpoint)
ACTION_TO_LONG_FORM = {
    "BO": "BOPEN",
    "SO": "SOPEN",
    "BC": "BCLOSE",
    "SC": "SCLOSE",
}


@dataclass
class AccountInfo:
    """Cached account metadata for API calls that require account context."""
    acct_num: str = ""
    acct_type: str = "Brokerage"
    acct_sub_type: str = "Brokerage"
    acct_sub_type_desc: str = ""
    name: str = ""
    reg_type_desc: str = ""
    option_level: int = 0
    is_margin: bool = False
    is_option: bool = False
    is_retirement: bool = False


@dataclass
class OptionLeg:
    """A single leg of a multi-leg options order."""
    symbol: str      # OCC symbol e.g. "SPXW260327P6375"
    action: str      # "BO" (Buy), "SO" (Sell) — direction only
    quantity: int    # number of contracts
    option_type: str = "O"  # "O" for open, "C" for close


class FidelityAPIClient:
    """
    Direct HTTP client for Fidelity's internal REST APIs.

    Uses session cookies extracted from Playwright for authentication.
    Data endpoints need only cookies; trading endpoints also need a CSRF token.
    """

    def __init__(self, cookies: dict[str, str], csrf_token: str = None):
        """
        Parameters
        ----------
        cookies : dict
            Cookie name -> value mapping from a valid Fidelity session.
        csrf_token : str, optional
            CSRF token for protected endpoints. If not provided, will be
            fetched automatically on first use.
        """
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        # Set cookies on session
        for name, value in cookies.items():
            self.session.cookies.set(name, value, domain=".fidelity.com")

        self._csrf_token = csrf_token
        self._account_info: Optional[AccountInfo] = None
        self._accounts: list[AccountInfo] = []

    @classmethod
    def from_automation(cls, automation) -> "FidelityAPIClient":
        """
        Create an API client from an active FidelityAutomation instance.

        Extracts cookies from the Playwright browser context.

        Parameters
        ----------
        automation : FidelityAutomation
            An active, logged-in FidelityAutomation instance.
        """
        # Extract cookies from Playwright context
        cookies = {}
        for cookie in automation.context.cookies():
            cookies[cookie["name"]] = cookie["value"]

        return cls(cookies)

    @classmethod
    def from_storage_state(cls, path: str) -> "FidelityAPIClient":
        """
        Create an API client from a saved Playwright storage state JSON file.

        Parameters
        ----------
        path : str
            Path to a Fidelity_*.json storage state file.
        """
        with open(path) as f:
            state = json.load(f)

        cookies = {}
        for cookie in state.get("cookies", []):
            cookies[cookie["name"]] = cookie["value"]

        return cls(cookies)

    # --- Session management ---

    def refresh_cookies(self, automation):
        """Refresh cookies from an active browser session."""
        for cookie in automation.context.cookies():
            self.session.cookies.set(
                cookie["name"], cookie["value"], domain=".fidelity.com"
            )
        self._csrf_token = None  # Force re-fetch

    def get_csrf_token(self) -> str:
        """Fetch a CSRF token from Fidelity's token endpoint."""
        if self._csrf_token:
            return self._csrf_token

        url = BASE_URL + ENDPOINTS["csrf_token"]
        resp = self.session.get(url)
        resp.raise_for_status()
        data = resp.json()
        self._csrf_token = data["csrfToken"]
        return self._csrf_token

    def _csrf_headers(self) -> dict:
        """Get headers with CSRF token for protected endpoints."""
        token = self.get_csrf_token()
        return {"X-CSRF-TOKEN": token}

    def _trade_headers(self) -> dict:
        """Get headers with CSRF token and trade-options Referer."""
        headers = self._csrf_headers()
        headers["Referer"] = TRADE_REFERER
        return headers

    def is_session_valid(self) -> bool:
        """Check if the current session cookies are still valid."""
        try:
            url = BASE_URL + ENDPOINTS["quotes"]
            resp = self.session.get(url, params={"symbols": ".SPX"}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return "quoteResponse" in data
            return False
        except Exception:
            return False

    # --- Account discovery ---

    def discover_accounts(self) -> list[AccountInfo]:
        """
        Discover all accounts and their capabilities.
        Uses the account-fusion endpoint from the trade-options API.
        """
        if self._accounts:
            return self._accounts

        url = BASE_URL + ENDPOINTS["trade_account_fusion"]
        headers = self._trade_headers()
        resp = self.session.post(url, json={}, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        accounts = []
        for acct in data:
            details = acct.get("accountDetails", {})
            info = AccountInfo(
                acct_num=acct.get("acctNum", ""),
                acct_type=details.get("acctType", ""),
                acct_sub_type=details.get("acctSubType", ""),
                acct_sub_type_desc=details.get("acctSubTypeDesc", ""),
                name=details.get("name", ""),
                reg_type_desc=details.get("regTypeDesc", ""),
                option_level=acct.get("optionLevel", 0),
                is_margin=acct.get("isMarginEstb", False),
                is_option=acct.get("isOptionEstb", False),
                is_retirement=details.get("isRetirement", False),
            )
            accounts.append(info)

        self._accounts = accounts

        # Set default account to the first options-enabled brokerage
        for acct in accounts:
            if acct.is_option and not acct.is_retirement:
                self._account_info = acct
                break

        if not self._account_info and accounts:
            self._account_info = accounts[0]

        return accounts

    def get_account(self, acct_num: str = None) -> AccountInfo:
        """Get account info. Discovers accounts if needed."""
        if not self._accounts:
            self.discover_accounts()

        if acct_num:
            for acct in self._accounts:
                if acct.acct_num == acct_num:
                    return acct
            raise ValueError(f"Account {acct_num} not found")

        if self._account_info:
            return self._account_info

        raise ValueError("No accounts discovered. Call discover_accounts() first.")

    # --- Quote APIs ---

    def get_quote(self, symbol: str) -> dict:
        """
        Get a real-time quote for a symbol.

        Parameters
        ----------
        symbol : str
            Symbol to quote. Use ".SPX" for S&P 500, ".VIX" for VIX.

        Returns
        -------
        dict with keys: lastPrice, dayHigh, dayLow, volume, netChgToday,
        pctChgToday, prevClosePrice, openPrice, etc.
        """
        url = BASE_URL + ENDPOINTS["quotes"]
        resp = self.session.get(url, params={"symbols": symbol})
        resp.raise_for_status()
        data = resp.json()

        for quote_item in data.get("quoteResponse", []):
            status = quote_item.get("status")
            # API returns status as either "0" (string) or {"errorCode": 0} (dict)
            is_ok = (
                status == "0"
                or status == 0
                or (isinstance(status, dict) and status.get("errorCode") == 0)
            )
            if is_ok:
                return quote_item.get("quoteData", {})

        return {}

    def get_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """
        Get real-time quotes for multiple symbols.

        Returns dict mapping symbol -> quoteData.
        """
        url = BASE_URL + ENDPOINTS["quotes"]
        symbols_str = ",".join(symbols)
        resp = self.session.get(url, params={"symbols": symbols_str})
        resp.raise_for_status()
        data = resp.json()

        result = {}
        for quote_item in data.get("quoteResponse", []):
            sym = quote_item.get("requestSymbol", "")
            qd = quote_item.get("quoteData", {})
            if qd:
                result[sym] = qd

        return result

    def get_spx_price(self) -> Optional[float]:
        """Get current SPX index price."""
        qd = self.get_quote(".SPX")
        price_str = qd.get("lastPrice", "")
        if price_str:
            return float(price_str)
        return None

    def get_vix_price(self) -> Optional[float]:
        """Get current VIX price."""
        qd = self.get_quote(".VIX")
        price_str = qd.get("lastPrice", "")
        if price_str:
            return float(price_str)
        return None

    # --- Option Chain APIs ---

    def get_option_expirations(self, symbol: str = ".SPX") -> list[dict]:
        """
        Get available option expiration dates.

        Returns list of dicts with keys: date, optionPeriodicity, setType, key.
        """
        url = BASE_URL + ENDPOINTS["option_expirations"]
        resp = self.session.get(url, params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        return data.get("expirations", [])

    def get_0dte_expiration(self, symbol: str = ".SPX") -> Optional[str]:
        """
        Get today's 0DTE expiration date string.

        Returns the date in whatever format the expirations API provides
        (currently YYYY-MM-DD). Returns None if no expiration available today.
        """
        expirations = self.get_option_expirations(symbol)
        # The earliest expiration is today's 0DTE (or the next available)
        if expirations:
            return expirations[0].get("date", "")
        return None

    def get_option_chain(
        self,
        symbol: str = "SPX",
        expiration_dates: list[str] = None,
        strikes: str = "All",
    ) -> list[dict]:
        """
        Get the full option chain with Greeks.

        Parameters
        ----------
        symbol : str
            Underlying symbol (e.g., "SPX").
        expiration_dates : list[str], optional
            List of expiration dates (YYYY-MM-DD or MM/DD/YYYY).
            If None, uses 0DTE expiration.
        strikes : str
            Number of strikes or "All" for full chain. Default "All".

        Returns
        -------
        list[dict] where each dict has keys:
            expirationData: {date, contractType, daysToExpiration, settlementType, optionPeriodicity}
            strike: str (e.g., "6650.00")
            callBid, callAsk, callBidSize, callAskSize: str
            callDelta, callGamma, callTheta, callVega, callRho: str
            callImpliedVolatility, callVolume, callOpenInterest: str
            callSelection: str (OCC symbol, e.g., "-SPXW260313C6650")
            putBid, putAsk, putBidSize, putAskSize: str
            putDelta, putGamma, putTheta, putVega, putRho: str
            putImpliedVolatility, putVolume, putOpenInterest: str
            putSelection: str (OCC symbol, e.g., "-SPXW260313P6650")
        """
        # Fetch all expirations to build settlementTypes param
        dotted = f".{symbol}" if not symbol.startswith(".") else symbol
        all_expirations = self.get_option_expirations(dotted)

        # Build a lookup: date -> expiration metadata
        exp_lookup = {e["date"]: e for e in all_expirations}

        if expiration_dates is None:
            if all_expirations:
                expiration_dates = [all_expirations[0]["date"]]
            else:
                expiration_dates = []

        # Convert dates to MM/DD/YYYY and build settlementTypes
        formatted_dates = []
        settlement_parts = []
        for date_str in expiration_dates:
            # Normalize to MM/DD/YYYY
            if "-" in date_str:
                # YYYY-MM-DD -> MM/DD/YYYY
                parts = date_str.split("-")
                mm_dd_yyyy = f"{parts[1]}/{parts[2]}/{parts[0]}"
            else:
                mm_dd_yyyy = date_str

            formatted_dates.append(mm_dd_yyyy)

            # Build settlement type: "Mon DD YYYYsetType|periodicity"
            # e.g., "Mar 30 2026P|W"
            exp_meta = exp_lookup.get(date_str)
            if exp_meta:
                # Parse date for month name
                if "-" in date_str:
                    y, m, d = date_str.split("-")
                else:
                    m, d, y = mm_dd_yyyy.split("/")

                month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                mon = month_names[int(m) - 1]
                set_type = exp_meta.get("setType", "P")
                periodicity = exp_meta.get("optionPeriodicity", "W")
                settlement_parts.append(f"{mon} {d.zfill(2)} {y}{set_type}|{periodicity}")

        dates_param = ",".join(formatted_dates)
        settlement_param = ",".join(settlement_parts)

        # Strip leading dot from symbol for the query param
        clean_symbol = symbol.lstrip(".")

        url = BASE_URL + ENDPOINTS["slo_chain"]
        params = {
            "strikes": strikes,
            "expirationDates": dates_param,
            "settlementTypes": settlement_param,
            "symbol": clean_symbol,
            "adjustedOptionsData": "true",
        }

        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        return data.get("callsAndPuts", [])

    def get_option_chain_parsed(
        self,
        symbol: str = "SPX",
        expiration_dates: list[str] = None,
    ) -> list[dict]:
        """
        Get option chain with numeric values (parsed from strings).

        Returns the same data as get_option_chain but with float values
        instead of strings, and additional computed fields.
        """
        raw_chain = self.get_option_chain(symbol, expiration_dates)
        parsed = []

        for row in raw_chain:
            item = {
                "strike": _parse_float(row.get("strike", "")),
                "expiration": row.get("expirationData", {}).get("date", ""),
                "dte": _parse_int(row.get("expirationData", {}).get("daysToExpiration", "")),
                "settlement": row.get("expirationData", {}).get("settlementType", ""),
                # Call side
                "call_bid": _parse_float(row.get("callBid", "")),
                "call_ask": _parse_float(row.get("callAsk", "")),
                "call_bid_size": _parse_int(row.get("callBidSize", "")),
                "call_ask_size": _parse_int(row.get("callAskSize", "")),
                "call_last": _parse_float(row.get("callLast", "")),
                "call_volume": _parse_int(row.get("callVolume", "")),
                "call_oi": _parse_int(row.get("callOpenInterest", "")),
                "call_delta": _parse_float(row.get("callDelta", "")),
                "call_gamma": _parse_float(row.get("callGamma", "")),
                "call_theta": _parse_float(row.get("callTheta", "")),
                "call_vega": _parse_float(row.get("callVega", "")),
                "call_rho": _parse_float(row.get("callRho", "")),
                "call_iv": _parse_float(row.get("callImpliedVolatility", "")),
                "call_symbol": row.get("callSelection", ""),
                # Put side
                "put_bid": _parse_float(row.get("putBid", "")),
                "put_ask": _parse_float(row.get("putAsk", "")),
                "put_bid_size": _parse_int(row.get("putBidSize", "")),
                "put_ask_size": _parse_int(row.get("putAskSize", "")),
                "put_last": _parse_float(row.get("putLast", "")),
                "put_volume": _parse_int(row.get("putVolume", "")),
                "put_oi": _parse_int(row.get("putOpenInterest", "")),
                "put_delta": _parse_float(row.get("putDelta", "")),
                "put_gamma": _parse_float(row.get("putGamma", "")),
                "put_theta": _parse_float(row.get("putTheta", "")),
                "put_vega": _parse_float(row.get("putVega", "")),
                "put_rho": _parse_float(row.get("putRho", "")),
                "put_iv": _parse_float(row.get("putImpliedVolatility", "")),
                "put_symbol": row.get("putSelection", ""),
            }

            # Computed fields
            if item["call_bid"] and item["call_ask"]:
                item["call_mid"] = round((item["call_bid"] + item["call_ask"]) / 2, 2)
                item["call_spread"] = round(item["call_ask"] - item["call_bid"], 2)
            else:
                item["call_mid"] = None
                item["call_spread"] = None

            if item["put_bid"] and item["put_ask"]:
                item["put_mid"] = round((item["put_bid"] + item["put_ask"]) / 2, 2)
                item["put_spread"] = round(item["put_ask"] - item["put_bid"], 2)
            else:
                item["put_mid"] = None
                item["put_spread"] = None

            parsed.append(item)

        return parsed

    # --- Volatility & Statistics ---

    def get_volatility(self, symbol: str = "SPX") -> dict:
        """Get historical and implied volatility data (HV10/30/60, IV30/60)."""
        url = BASE_URL + ENDPOINTS["volatility_extended"]
        resp = self.session.get(url, params={"underlying": symbol})
        resp.raise_for_status()
        return resp.json()

    def get_key_statistics(self, symbol: str = "SPX") -> dict:
        """Get option statistics: IV percentile, volume, OI, biggest trades."""
        url = BASE_URL + ENDPOINTS["key_statistics"]
        resp = self.session.get(url, params={"underlying": symbol})
        resp.raise_for_status()
        return resp.json()

    # --- Account & Position APIs (CSRF required) ---

    def get_balances(self, acct_num: str = None) -> dict:
        """
        Get account balances and buying power.

        Returns dict with keys: totalAcctVal, cashAvailForTrade,
        intraDayBP, mrgnBP, nonMrgnBP, isMrgnAcct, etc.
        """
        acct = self.get_account(acct_num)

        url = BASE_URL + ENDPOINTS["trade_balances"]
        body = {
            "account": {
                "acctNum": acct.acct_num,
                "isDefaultAcct": False,
                "accountDetails": {
                    "acctType": acct.acct_type,
                    "acctSubType": acct.acct_sub_type,
                    "acctSubTypeDesc": acct.acct_sub_type_desc,
                    "name": acct.name,
                    "regTypeDesc": acct.reg_type_desc,
                    "relTypeCode": "INDIVIDUAL",
                    "hiddenInd": False,
                    "isAdvisorAcct": False,
                    "isAuthorizedAcct": False,
                    "isRetirement": acct.is_retirement,
                },
                "optionLevel": acct.option_level,
                "isMarginEstb": acct.is_margin,
                "isOptionEstb": acct.is_option,
                "accountFeatures": {
                    "optionDetail": {
                        "isCoveredWriting": False,
                        "isTradePurchases": False,
                        "isSpreadNCombinationForEquity": False,
                        "isAllIndexNEquityXIndexOption": False,
                        "isAllIndexNEquity": acct.option_level >= 5,
                    },
                    "isMultiCurrencyInd": True,
                    "spreadsAllowedInd": False,
                },
            }
        }

        headers = self._csrf_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_positions(self, acct_num: str = None) -> dict:
        """
        Get open positions including option contracts.

        Returns dict with keys:
            positionDetails: list of position dicts
            hasOwnedOptionPosition: bool
            hasOwnedEquityETFPosition: bool
        """
        acct = self.get_account(acct_num)

        url = BASE_URL + ENDPOINTS["trade_positions"]
        body = {
            "acctNum": acct.acct_num,
            "acctType": acct.acct_type,
            "acctSubType": acct.acct_sub_type,
            "retirementInd": acct.is_retirement,
        }

        headers = self._csrf_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_option_positions(self, acct_num: str = None) -> list[dict]:
        """Get only option positions (filtered from all positions)."""
        positions = self.get_positions(acct_num)
        return [
            p for p in positions.get("positionDetails", [])
            if p.get("securityType") == "Option"
        ]

    def get_rules_engine(self) -> dict:
        """
        Get account trading rules: option level, allowed strategies.

        Returns dict with keys: accountSeeding, strategiesByOptionLevel, strategyRules.
        """
        url = BASE_URL + ENDPOINTS["trade_rules_engine"]
        headers = self._csrf_headers()
        resp = self.session.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # --- Multi-leg Chain ---

    def get_mlo_chain(
        self,
        symbol: str = "SPX",
        strategy: str = "Spread",
        expiration1: str = None,
        set_type1: str = "P",
        strikes: int = 10,
    ) -> dict:
        """
        Get multi-leg options chain (pre-built spreads).

        Parameters
        ----------
        strategy : str
            Strategy type. Common values: "Spread", "Straddle", "Strangle".
        expiration1 : str
            Expiration date in "YYYY-MM-DD" format.
        set_type1 : str
            "P" for PM settlement (standard), "A" for AM settlement.
        """
        url = BASE_URL + ENDPOINTS["mlo_chain"]
        params = {
            "strikes": strikes,
            "expiration1": expiration1 or "",
            "setType1": set_type1,
            "expiration2": "",
            "setType2": "",
        }
        if strategy:
            params["strategy"] = strategy

        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # --- Options Order Placement ---

    def _build_order_payload(
        self,
        legs: list,
        limit_price: float,
        strategy_type: str,
        debit_credit: str,
        time_in_force: str,
        req_type_code: str,
        acct_num: str = None,
        conf_num: str = None,
        original_order_id: str = None,
    ) -> dict:
        """Build the orderDetails payload for mlo-verify/mlo-confirm.

        Parameters
        ----------
        legs : list[OptionLeg]
        limit_price : float
        strategy_type : str
            "CD" (Condor), "SP" (Spread), "ST" (Straddle), "SG" (Strangle).
        debit_credit : str
            "CR" (Credit), "DB" (Debit).
        time_in_force : str
            "D" (Day), "GTC" (Good Till Cancel).
        req_type_code : str
            "N" for verify (preview), "P" for confirm (place).
        acct_num : str, optional
        conf_num : str, optional
            Confirmation number from verify response. Required for confirm.
        original_order_id : str, optional
            Original order confNum for replace orders.
        """
        acct = self.get_account(acct_num)
        is_replace = original_order_id is not None

        acct_type_code = "M" if acct.is_margin else "C"

        order = {
            "acctNum": acct.acct_num,
            "tif": time_in_force,
            "netAmount": f"{limit_price:.2f}",
            "aonCode": False,
            "acctTypeCode": acct_type_code,
            "reqTypeCode": req_type_code,
            "numOfLegs": str(len(legs)),
            "dbCrEvenCode": debit_credit,
            "strategyType": strategy_type,
        }

        if conf_num:
            order["confNum"] = conf_num

        if original_order_id:
            order["orderNumOrig"] = original_order_id

        for i, leg in enumerate(legs, 1):
            qty = str(leg.quantity) if is_replace else leg.quantity
            order[f"leg{i}"] = {
                "action": leg.action,
                "type": leg.option_type,
                "qty": qty,
                "symbol": leg.symbol,
            }

        return {"orderDetails": order}

    def preview_option_order(
        self,
        legs: list,
        limit_price: float,
        strategy_type: str = "CD",
        debit_credit: str = "CR",
        time_in_force: str = "D",
        acct_num: str = None,
    ) -> dict:
        """Preview a multi-leg options order without placing it.

        Calls mlo-verify to validate the order and return cost estimates,
        warnings, and a confNum (used by place_option_order to submit).

        Parameters
        ----------
        legs : list[OptionLeg]
            Order legs (1-4 legs).
        limit_price : float
            Limit price for the order.
        strategy_type : str
            "CD" (Condor), "SP" (Spread), "ST" (Straddle), "SG" (Strangle).
        debit_credit : str
            "CR" (Credit) or "DB" (Debit).
        time_in_force : str
            "D" (Day) or "GTC" (Good Till Cancel).
        acct_num : str, optional
            Account number. Uses default account if not provided.

        Returns
        -------
        dict with keys:
            verifyDetails: {acctNum, orderConfirmDetail: {confNum, ...}, tifCode, ...}
            messages: list of {message, detail, type} dicts (warnings/errors)
        """
        body = self._build_order_payload(
            legs=legs,
            limit_price=limit_price,
            strategy_type=strategy_type,
            debit_credit=debit_credit,
            time_in_force=time_in_force,
            req_type_code="N",
            acct_num=acct_num,
        )

        url = BASE_URL + ENDPOINTS["mlo_verify"]
        headers = self._trade_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_order_status(self, order_id: str, acct_num: str = None) -> dict:
        """Check the status of an options order.

        Parameters
        ----------
        order_id : str
            The confirmation number (confNum) of the order.
        acct_num : str, optional
            Account number. Uses default account if not provided.

        Returns
        -------
        dict with key:
            orderDetails: list of per-leg dicts with statusCode, statusDesc,
            decodeStatus, cancelableInd, replaceableInd, limitPrice, strategyName,
            and orderLegInfoDetail.
        """
        acct = self.get_account(acct_num)

        url = BASE_URL + ENDPOINTS["trade_orders"]
        body = {"orderId": order_id, "acctNum": acct.acct_num}
        headers = self._trade_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_net_debit_credit(
        self,
        legs: list,
        limit_price: float = None,
        strategy: str = "Iron Condor",
        strategy_type: str = "CD",
        debit_credit: str = "CR",
        price_type: str = "L",
        acct_num: str = None,
    ) -> dict:
        """Calculate net bid/ask/mid and estimated commissions for a set of legs.

        Fetches current quotes for each leg automatically to populate bid/ask.

        Parameters
        ----------
        legs : list[OptionLeg]
            Order legs (1-4 legs).
        limit_price : float, optional
            Limit price. Required when price_type="L".
        strategy : str
            Human-readable strategy name (e.g., "Iron Condor", "Spread").
        strategy_type : str
            Strategy code: "CD", "SP", "ST", "SG".
        debit_credit : str or None
            "CR" or "DB". Pass None to omit (e.g., for initial mid-price calc).
        price_type : str
            "L" (Limit) or "M" (Mid/Market).
        acct_num : str, optional

        Returns
        -------
        dict with keys: acctNum, netBid, netAsk, mid, estComm, totalCost (str),
        gcd, netDebitOrCredit.
        """
        acct = self.get_account(acct_num)
        acct_type_code = "M" if acct.is_margin else "C"

        # Fetch current quotes for bid/ask
        # Trade-options quotes endpoint requires dash-prefixed symbols
        symbols = [f"-{leg.symbol}" for leg in legs]
        quotes_url = BASE_URL + ENDPOINTS["trade_quotes"]
        quotes_headers = self._trade_headers()
        quotes_resp = self.session.post(
            quotes_url, json={"symbols": symbols}, headers=quotes_headers
        )
        quotes_resp.raise_for_status()
        quotes_data = quotes_resp.json()

        # Build symbol -> {bid, ask} lookup (response strips the dash)
        quote_lookup = {}
        for q in quotes_data.get("quotes", []):
            sym = q.get("symbol", "").lstrip("-")
            quote_lookup[sym] = {
                "bid": str(q.get("bid", "0")),
                "ask": str(q.get("ask", "0")),
            }

        body = {
            "numOfLegs": str(len(legs)),
            "acctNum": acct.acct_num,
            "acctTypeCode": acct_type_code,
            "dbCrEvenCode": debit_credit,
            "strategy": strategy,
            "strategyType": strategy_type,
            "priceTypeCode": price_type,
        }

        for i, leg in enumerate(legs, 1):
            q = quote_lookup.get(leg.symbol, {"bid": "0", "ask": "0"})
            body[f"action{i}"] = leg.action
            body[f"bid{i}"] = q["bid"]
            body[f"ask{i}"] = q["ask"]
            body[f"leg{i}"] = {
                "orderAction": _action_to_order_action(leg.action, leg.symbol),
                "qty": leg.quantity,
                "symbol": leg.symbol,
                "optionType": leg.option_type,
            }

        if price_type == "L" and limit_price is not None:
            body["limitPrice"] = f"{limit_price:.2f}"

        url = BASE_URL + ENDPOINTS["net_debit_credit"]
        headers = self._trade_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_max_gain_loss(
        self,
        legs: list,
        underlying_symbol: str = ".SPX",
        strategy_type: str = "CD",
        limit_price: float = 0.0,
    ) -> dict:
        """Calculate max gain, max loss, and breakeven for a strategy.

        Parameters
        ----------
        legs : list[OptionLeg]
        underlying_symbol : str
            Underlying index/stock symbol (e.g., ".SPX").
        strategy_type : str
            Strategy code: "CD", "SP", "ST", "SG".
        limit_price : float
            Net limit price (used for the first leg's price field).

        Returns
        -------
        dict with keys: maxGain, maxLoss, maxGainNumber, maxLossNumber,
        breakEvenPoint, containsCloseAction.
        """
        leg_details = []
        for i, leg in enumerate(legs):
            action_long = ACTION_TO_LONG_FORM.get(leg.action, leg.action)
            # First leg gets the negative limit price, rest get "0.00"
            price = f"-{limit_price:.2f}" if i == 0 else "0.00"
            # Sells have negative qty
            qty = -leg.quantity if leg.action.startswith("S") else leg.quantity
            leg_details.append({
                "symbol": leg.symbol,
                "orderActionCode": action_long,
                "price": price,
                "qty": qty,
            })

        body = {
            "underlyingSymbol": underlying_symbol,
            "legDetails": leg_details,
            "strategyType": strategy_type,
        }

        url = BASE_URL + ENDPOINTS["max_gain_loss"]
        headers = self._trade_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def place_option_order(
        self,
        legs: list,
        limit_price: float,
        strategy_type: str = "CD",
        debit_credit: str = "CR",
        time_in_force: str = "D",
        acct_num: str = None,
        dry_run: bool = True,
    ) -> dict:
        """Preview and optionally place a multi-leg options order.

        Parameters
        ----------
        legs : list[OptionLeg]
            Order legs (1-4 legs).
        limit_price : float
            Limit price for the order.
        strategy_type : str
            "CD" (Condor), "SP" (Spread), "ST" (Straddle), "SG" (Strangle).
        debit_credit : str
            "CR" (Credit) or "DB" (Debit).
        time_in_force : str
            "D" (Day) or "GTC" (Good Till Cancel).
        acct_num : str, optional
        dry_run : bool
            If True (default), only previews the order. If False, places it.

        Returns
        -------
        dict: Preview result (if dry_run) or confirmation result (if live).
            Preview has keys: verifyDetails, messages
            Confirm has keys: confirmDetails, messages
        """
        # Phase 1: Preview
        preview = self.preview_option_order(
            legs=legs,
            limit_price=limit_price,
            strategy_type=strategy_type,
            debit_credit=debit_credit,
            time_in_force=time_in_force,
            acct_num=acct_num,
        )

        if dry_run:
            return preview

        # Check for errors in preview
        messages = preview.get("messages", [])
        errors = [m for m in messages if m.get("type") == "error"]
        if errors:
            raise ValueError(
                f"Order preview failed: {errors[0].get('detail', errors[0].get('message'))}"
            )

        # Phase 2: Confirm — extract confNum and re-send with reqTypeCode="P"
        conf_num = preview["verifyDetails"]["orderConfirmDetail"]["confNum"]

        body = self._build_order_payload(
            legs=legs,
            limit_price=limit_price,
            strategy_type=strategy_type,
            debit_credit=debit_credit,
            time_in_force=time_in_force,
            req_type_code="P",
            acct_num=acct_num,
            conf_num=conf_num,
        )

        url = BASE_URL + ENDPOINTS["mlo_confirm"]
        headers = self._trade_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def close_option_order(
        self,
        open_legs: list,
        limit_price: float,
        time_in_force: str = "D",
        acct_num: str = None,
        dry_run: bool = True,
    ) -> dict:
        """Close an existing multi-leg options position.

        Automatically reverses the open legs: flips BO<->SO actions,
        sets type="C" (close), uses strategyType="CU" (close/unwind),
        and orders legs by descending strike (as Fidelity requires for close).

        Parameters
        ----------
        open_legs : list[OptionLeg]
            The original legs used to open the position (with action BO/SO
            and option_type "O"). This method will reverse them.
        limit_price : float
            Limit price for the close order (debit).
        time_in_force : str
            "D" (Day) or "GTC" (Good Till Cancel).
        acct_num : str, optional
        dry_run : bool
            If True (default), only previews. If False, places the close.

        Returns
        -------
        dict: Preview result (if dry_run) or confirmation result (if live).
        """
        # Reverse the legs: flip BO<->SO, set type="C"
        close_legs = []
        for leg in open_legs:
            reverse_action = "SO" if leg.action == "BO" else "BO"
            close_legs.append(OptionLeg(
                symbol=leg.symbol,
                action=reverse_action,
                quantity=leg.quantity,
                option_type="C",  # Close
            ))

        # Sort by descending strike (Fidelity requires this for close orders)
        # Extract strike from OCC symbol: digits after P or C at end
        def _strike_from_symbol(sym):
            match = re.search(r'[PC](\d+)$', sym)
            return int(match.group(1)) if match else 0

        close_legs.sort(key=lambda l: _strike_from_symbol(l.symbol), reverse=True)

        # Use place_option_order with strategyType="CU" and debit_credit="DB"
        preview = self.preview_option_order(
            legs=close_legs,
            limit_price=limit_price,
            strategy_type="CU",
            debit_credit="DB",
            time_in_force=time_in_force,
            acct_num=acct_num,
        )

        if dry_run:
            return preview

        messages = preview.get("messages", [])
        errors = [m for m in messages if m.get("type") == "error"]
        if errors:
            raise ValueError(
                f"Close preview failed: {errors[0].get('detail', errors[0].get('message'))}"
            )

        conf_num = preview["verifyDetails"]["orderConfirmDetail"]["confNum"]

        body = self._build_order_payload(
            legs=close_legs,
            limit_price=limit_price,
            strategy_type="CU",
            debit_credit="DB",
            time_in_force=time_in_force,
            req_type_code="P",
            acct_num=acct_num,
            conf_num=conf_num,
        )

        url = BASE_URL + ENDPOINTS["mlo_confirm"]
        headers = self._trade_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def replace_option_order(
        self,
        original_order_id: str,
        legs: list,
        limit_price: float,
        strategy_type: str = "CD",
        debit_credit: str = "CR",
        time_in_force: str = "D",
        acct_num: str = None,
        dry_run: bool = True,
    ) -> dict:
        """Preview and optionally replace an existing open options order.

        Parameters
        ----------
        original_order_id : str
            The confNum of the order to replace.
        legs : list[OptionLeg]
            Order legs (same symbols as original, new price/qty).
        limit_price : float
            New limit price.
        strategy_type, debit_credit, time_in_force, acct_num : same as place.
        dry_run : bool
            If True (default), only previews. If False, executes replacement.

        Returns
        -------
        dict: Preview result (if dry_run) or confirmation result (if live).
        """
        # Phase 1: Verify replace
        body = self._build_order_payload(
            legs=legs,
            limit_price=limit_price,
            strategy_type=strategy_type,
            debit_credit=debit_credit,
            time_in_force=time_in_force,
            req_type_code="N",
            acct_num=acct_num,
            original_order_id=original_order_id,
        )

        url = BASE_URL + ENDPOINTS["mlo_verify_replace"]
        headers = self._trade_headers()
        resp = self.session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        preview = resp.json()

        if dry_run:
            return preview

        # Check for errors
        messages = preview.get("messages", [])
        errors = [m for m in messages if m.get("type") == "error"]
        if errors:
            raise ValueError(
                f"Replace preview failed: {errors[0].get('detail', errors[0].get('message'))}"
            )

        # Phase 2: Confirm replace
        conf_num = preview["verifyDetails"]["orderConfirmDetail"]["confNum"]

        confirm_body = self._build_order_payload(
            legs=legs,
            limit_price=limit_price,
            strategy_type=strategy_type,
            debit_credit=debit_credit,
            time_in_force=time_in_force,
            req_type_code="P",
            acct_num=acct_num,
            conf_num=conf_num,
            original_order_id=original_order_id,
        )

        url = BASE_URL + ENDPOINTS["mlo_confirm_replace"]
        resp = self.session.post(url, json=confirm_body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # --- Convenience methods for iron condor trading ---

    def get_ic_chain_data(
        self,
        underlying_price: float = None,
        expiration_dates: list[str] = None,
        otm_range: float = 200.0,
    ) -> list[dict]:
        """
        Get option chain data filtered for iron condor strike selection.

        Returns parsed chain data for OTM puts and calls within range
        of the underlying price.

        Parameters
        ----------
        underlying_price : float, optional
            Current underlying price. If None, fetches from quote API.
        expiration_dates : list[str], optional
            Expiration dates. If None, uses 0DTE.
        otm_range : float
            How far OTM to include (points from underlying). Default 200.
        """
        if underlying_price is None:
            underlying_price = self.get_spx_price()
            if underlying_price is None:
                raise ValueError("Could not fetch SPX price")

        chain = self.get_option_chain_parsed("SPX", expiration_dates)

        # Filter to relevant strikes
        lower_bound = underlying_price - otm_range
        upper_bound = underlying_price + otm_range

        filtered = [
            row for row in chain
            if row["strike"] is not None
            and lower_bound <= row["strike"] <= upper_bound
        ]

        return filtered


# --- Helper functions ---

def _parse_float(value: str) -> Optional[float]:
    """Parse a string to float, returning None for empty/invalid values."""
    if not value or value.strip() in ("", "--", "N/A"):
        return None
    try:
        return float(value.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_int(value: str) -> Optional[int]:
    """Parse a string to int, returning None for empty/invalid values."""
    if not value or value.strip() in ("", "--", "N/A"):
        return None
    try:
        return int(value.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _get_put_call(symbol: str) -> str:
    """Extract 'P' or 'C' from an OCC option symbol.

    OCC format: SYMBOL + YYMMDD + P/C + strike
    e.g. SPXW260327P6375 -> 'P', SPXW260327C6390 -> 'C'
    """
    match = re.search(r'\d{6}([PC])', symbol)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot parse put/call from symbol: {symbol}")


def _action_to_order_action(action: str, symbol: str) -> str:
    """Convert OptionLeg action + symbol to net-debit-credit orderAction code.

    Maps buy/sell direction + put/call type to: BP, SP, BC, SC.
    Note: BC = Buy Call, SC = Sell Call (direction + type, NOT open/close).
    """
    direction = action[0]  # 'B' or 'S'
    put_call = _get_put_call(symbol)  # 'P' or 'C'
    return direction + put_call
