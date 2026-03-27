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

        url = BASE_URL + ENDPOINTS["account_context"]
        resp = self.session.post(url, json={})
        resp.raise_for_status()
        data = resp.json()

        accounts = []
        for acct in data.get("acctDetails", []):
            info = AccountInfo(
                acct_num=acct.get("acctNum", ""),
                acct_type=acct.get("acctType", ""),
                acct_sub_type=acct.get("acctSubType", ""),
                acct_sub_type_desc=acct.get("acctSubTypeDesc", ""),
                name=acct.get("preferenceDetail", {}).get("acctNickName", ""),
                reg_type_desc=acct.get("acctSubTypeDesc", ""),
            )

            # Check for trading attributes
            trade_detail = acct.get("acctTradeAttrDetail", {})
            if trade_detail:
                info.option_level = trade_detail.get("optionLevel", 0)
                info.is_margin = trade_detail.get("mrgnEstb", False)
                info.is_option = trade_detail.get("optionEstb", False)

            info.is_retirement = acct.get("acctType", "") in ("IRA", "Roth IRA", "401k")
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
            if quote_item.get("status") == "0":  # 0 = success
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
        Get today's 0DTE expiration date string (MM/DD/YYYY format).

        Returns None if no 0DTE expiration available today.
        """
        expirations = self.get_option_expirations(symbol)
        for exp in expirations:
            if exp.get("daysToExpiration") == "0" or str(exp.get("daysToExpiration")) == "0":
                return exp.get("date", "")
            # Also check the key field which may have today's date
            if exp.get("key", "").startswith("0|"):
                return exp.get("date", "")
        # Fallback: find the earliest expiration
        if expirations:
            return expirations[0].get("date", "")
        return None

    def get_option_chain(
        self,
        symbol: str = "SPX",
        expiration_dates: list[str] = None,
        strikes: str = "All",
        settlement_types: str = "",
    ) -> list[dict]:
        """
        Get the full option chain with Greeks.

        Parameters
        ----------
        symbol : str
            Underlying symbol (e.g., "SPX").
        expiration_dates : list[str], optional
            List of expiration dates in "MM/DD/YYYY" format.
            If None, uses 0DTE expiration.
        strikes : str
            Number of strikes or "All" for full chain. Default "All".
        settlement_types : str
            Settlement type filter. Empty string for all.

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
        if expiration_dates is None:
            # Get 0DTE by default
            exp = self.get_0dte_expiration(f".{symbol}" if not symbol.startswith(".") else symbol)
            if exp:
                expiration_dates = [exp]
            else:
                expiration_dates = []

        # Format dates as comma-separated MM/DD/YYYY
        dates_param = ",".join(expiration_dates)

        url = BASE_URL + ENDPOINTS["slo_chain"]
        params = {
            "strikes": strikes,
            "expirationDates": dates_param,
            "settlementTypes": settlement_types,
        }
        # Add symbol to URL path if not SPX default
        if symbol and symbol.upper() not in ("SPX", ".SPX"):
            params["underlying"] = symbol

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
