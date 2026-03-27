# Options Order Placement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-leg options order placement (preview, place, replace, status check, pricing) to `FidelityAPIClient`.

**Architecture:** Extends the existing `FidelityAPIClient` class with 7 new trade-options endpoints and 6 public methods + 1 private helper. Uses the same CSRF+cookie auth pattern as existing trade methods. Two-phase commit: `mlo-verify` (preview) returns a `confNum`, `mlo-confirm` (place) echoes it back.

**Tech Stack:** Python 3.13, `requests`, `dataclasses`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-27-options-order-placement-design.md`

**Captured payloads:** `api_captures/options_order_trade_endpoints.json`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `fidelity/api_client.py` | Modify | Add endpoints, constants, `OptionLeg` dataclass, 7 methods |
| `test_api_client.py` | Modify | Add live validation tests for new methods |

---

### Task 1: Add endpoints and constants

**Files:**
- Modify: `fidelity/api_client.py:54-77` (ENDPOINTS dict)
- Modify: `fidelity/api_client.py` (new constants after ENDPOINTS)

- [ ] **Step 1: Add 8 new endpoint entries to `ENDPOINTS` dict**

In `fidelity/api_client.py`, add these entries inside the `ENDPOINTS` dict after the existing `trade_autosuggest` entry (line 70):

```python
    # Multi-leg order endpoints (csrf + cookie)
    "mlo_verify": "/ftgw/digital/trade-options/api/mlo-verify",
    "mlo_confirm": "/ftgw/digital/trade-options/api/mlo-confirm",
    "mlo_verify_replace": "/ftgw/digital/trade-options/api/mlo-verify-replace",
    "mlo_confirm_replace": "/ftgw/digital/trade-options/api/mlo-confirm-replace",
    "trade_orders": "/ftgw/digital/trade-options/api/orders",
    "trade_quotes": "/ftgw/digital/trade-options/api/quotes",
    "net_debit_credit": "/ftgw/digital/trade-options/api/net-debit-credit",
    "max_gain_loss": "/ftgw/digital/trade-options/api/max-gain-loss",
```

- [ ] **Step 2: Add action code constants and trade Referer**

After the `DEFAULT_HEADERS` dict (line 86), add:

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add fidelity/api_client.py
git commit -m "feat: add options order endpoint constants and action code mappings"
```

---

### Task 2: Add `OptionLeg` dataclass and `_build_order_payload` helper

**Files:**
- Modify: `fidelity/api_client.py` (after `AccountInfo` dataclass, ~line 102)
- Modify: `fidelity/api_client.py` (new private method in `FidelityAPIClient`)

- [ ] **Step 1: Add `OptionLeg` dataclass**

After the `AccountInfo` class (after line 101), add:

```python
@dataclass
class OptionLeg:
    """A single leg of a multi-leg options order."""
    symbol: str      # OCC symbol e.g. "SPXW260327P6375"
    action: str      # "BO" (Buy Open), "SO" (Sell Open), "BC" (Buy Close), "SC" (Sell Close)
    quantity: int    # number of contracts
    option_type: str = "O"  # "O" for options
```

- [ ] **Step 2: Add helper to parse put/call from OCC symbol**

Add as a module-level function near the bottom with the other helpers (`_parse_float`, `_parse_int`):

```python
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
```

- [ ] **Step 3: Add `_build_order_payload` private method**

Add inside `FidelityAPIClient`, after the existing `get_mlo_chain` method (after line 650), in a new section:

```python
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
```

- [ ] **Step 4: Add `_trade_headers` helper**

Add inside `FidelityAPIClient`, right after `_csrf_headers` (after line 196):

```python
    def _trade_headers(self) -> dict:
        """Get headers with CSRF token and trade-options Referer."""
        headers = self._csrf_headers()
        headers["Referer"] = TRADE_REFERER
        return headers
```

- [ ] **Step 5: Commit**

```bash
git add fidelity/api_client.py
git commit -m "feat: add OptionLeg dataclass, order payload builder, and action code helpers"
```

---

### Task 3: Implement `preview_option_order` and `get_order_status`

**Files:**
- Modify: `fidelity/api_client.py` (new methods in `FidelityAPIClient`)

- [ ] **Step 1: Add `preview_option_order`**

Add after `_build_order_payload` in the `FidelityAPIClient` class:

```python
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
```

- [ ] **Step 2: Add `get_order_status`**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add fidelity/api_client.py
git commit -m "feat: add preview_option_order and get_order_status methods"
```

---

### Task 4: Implement `get_net_debit_credit` and `get_max_gain_loss`

**Files:**
- Modify: `fidelity/api_client.py` (new methods in `FidelityAPIClient`)

- [ ] **Step 1: Add `get_net_debit_credit`**

Add in the options order placement section:

```python
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
```

- [ ] **Step 2: Add `get_max_gain_loss`**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add fidelity/api_client.py
git commit -m "feat: add get_net_debit_credit and get_max_gain_loss methods"
```

---

### Task 5: Implement `place_option_order` and `replace_option_order`

**Files:**
- Modify: `fidelity/api_client.py` (new methods in `FidelityAPIClient`)

- [ ] **Step 1: Add `place_option_order`**

```python
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
```

- [ ] **Step 2: Add `replace_option_order`**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add fidelity/api_client.py
git commit -m "feat: add place_option_order and replace_option_order methods"
```

---

### Task 6: Add live validation tests

**Files:**
- Modify: `test_api_client.py` (add new test functions)

These tests call the real Fidelity API with a live session. Phase 1 tests are safe (read-only). Phase 2 tests use `dry_run=True` only.

- [ ] **Step 1: Add shared test helper for building IC legs**

Add at the bottom of `test_api_client.py`, before the `main()` function:

```python
def _build_test_ic_legs(client):
    """Build a 0DTE iron condor ~20 pts OTM for testing. Returns (legs, spx) or (None, None)."""
    from fidelity.api_client import OptionLeg
    spx = client.get_spx_price()
    if not spx:
        return None, None
    exp = client.get_0dte_expiration(".SPX")
    if not exp:
        return None, None

    lower_put = int(spx - 20) // 5 * 5
    upper_put = lower_put + 5
    lower_call = int(spx + 20) // 5 * 5
    upper_call = lower_call + 5

    parts = exp.split("/")
    date_str = f"{parts[2][2:]}{parts[0].zfill(2)}{parts[1].zfill(2)}"

    legs = [
        OptionLeg(symbol=f"SPXW{date_str}P{lower_put}", action="BO", quantity=1),
        OptionLeg(symbol=f"SPXW{date_str}P{upper_put}", action="SO", quantity=1),
        OptionLeg(symbol=f"SPXW{date_str}C{lower_call}", action="SO", quantity=1),
        OptionLeg(symbol=f"SPXW{date_str}C{upper_call}", action="BO", quantity=1),
    ]
    return legs, spx
```

- [ ] **Step 2: Add test for `preview_option_order`**

```python
def test_preview_option_order(client: FidelityAPIClient):
    """Test 11: Preview an iron condor order (read-only, no placement)."""
    print("\n--- Test: Preview Option Order (IC) ---")
    try:
        legs, spx = _build_test_ic_legs(client)
        if not legs:
            print("  SKIPPED: Could not build IC legs (no SPX price or 0DTE)")
            return False
        print(f"  SPX: {spx}")
        print(f"  Legs: {[l.symbol for l in legs]}")

        result = client.preview_option_order(
            legs=legs,
            limit_price=2.00,
            strategy_type="CD",
            debit_credit="CR",
        )

        verify = result.get("verifyDetails", {})
        conf = verify.get("orderConfirmDetail", {})
        print(f"  confNum: {conf.get('confNum', 'N/A')}")
        print(f"  Strategy: {conf.get('strategy', 'N/A')}")

        net_vals = conf.get("orderDetail", {}).get("netValues", {})
        print(f"  Net Bid: {net_vals.get('netBid', {}).get('value', 'N/A')}")
        print(f"  Net Ask: {net_vals.get('netAsk', {}).get('value', 'N/A')}")
        print(f"  Net Mid: {net_vals.get('netMid', {}).get('value', 'N/A')}")
        print(f"  Commission: {net_vals.get('netCommission', 'N/A')}")

        messages = result.get("messages", [])
        warnings = [m for m in messages if m.get("type") == "warning"]
        errors = [m for m in messages if m.get("type") == "error"]
        print(f"  Warnings: {len(warnings)}, Errors: {len(errors)}")

        return "confNum" in str(conf)
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_net_debit_credit(client: FidelityAPIClient):
    """Test 12: Calculate net debit/credit for an iron condor."""
    print("\n--- Test: Net Debit/Credit ---")
    try:
        legs, spx = _build_test_ic_legs(client)
        if not legs:
            print("  SKIPPED: Could not build IC legs")
            return False

        result = client.get_net_debit_credit(
            legs=legs,
            limit_price=2.00,
            strategy="Iron Condor",
            strategy_type="CD",
            debit_credit="CR",
        )

        print(f"  Net Bid: {result.get('netBid', 'N/A')}")
        print(f"  Net Ask: {result.get('netAsk', 'N/A')}")
        print(f"  Mid: {result.get('mid', 'N/A')}")
        print(f"  Est Commission: {result.get('estComm', 'N/A')}")
        print(f"  Total Cost: {result.get('totalCost', 'N/A')}")
        print(f"  Net D/C: {result.get('netDebitOrCredit', 'N/A')}")

        return "netBid" in result
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_max_gain_loss(client: FidelityAPIClient):
    """Test 13: Calculate max gain/loss for an iron condor."""
    print("\n--- Test: Max Gain/Loss ---")
    try:
        legs, spx = _build_test_ic_legs(client)
        if not legs:
            print("  SKIPPED: Could not build IC legs")
            return False

        result = client.get_max_gain_loss(
            legs=legs,
            underlying_symbol=".SPX",
            strategy_type="CD",
            limit_price=2.00,
        )

        print(f"  Max Gain: {result.get('maxGain', 'N/A')}")
        print(f"  Max Loss: {result.get('maxLoss', 'N/A')}")
        print(f"  Breakeven: {result.get('breakEvenPoint', 'N/A')}")

        return "maxGain" in result
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_place_option_order_dry_run(client: FidelityAPIClient):
    """Test 14: Dry-run place_option_order (preview only, no real order)."""
    print("\n--- Test: Place Option Order (dry_run=True) ---")
    try:
        legs, spx = _build_test_ic_legs(client)
        if not legs:
            print("  SKIPPED: Could not build IC legs")
            return False

        result = client.place_option_order(
            legs=legs,
            limit_price=2.00,
            strategy_type="CD",
            debit_credit="CR",
            dry_run=True,  # SAFE: preview only
        )

        # Should return the same as preview_option_order
        verify = result.get("verifyDetails", {})
        conf = verify.get("orderConfirmDetail", {})
        print(f"  confNum: {conf.get('confNum', 'N/A')}")
        print(f"  Strategy: {conf.get('strategy', 'N/A')}")
        print(f"  (dry_run=True, no order placed)")

        return "verifyDetails" in result
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
```

- [ ] **Step 3: Add new tests to the `main()` test runner**

In the `tests` list inside `main()`, add after the existing `("ic_chain", test_ic_chain)` entry:

```python
        ("preview_order", test_preview_option_order),
        ("net_debit_credit", test_net_debit_credit),
        ("max_gain_loss", test_max_gain_loss),
        ("place_order_dry", test_place_option_order_dry_run),
```

- [ ] **Step 4: Commit**

```bash
git add test_api_client.py
git commit -m "test: add live validation tests for options order placement"
```

---

### Task 7: Run validation and fix issues

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/u357086/Documents/Development/git/fidelity-api
.venv/bin/python test_api_client.py
```

Requires a valid session (saved cookies from a recent login). Expected: all 14 tests pass. The 4 new tests (#11-14) only work during market hours when 0DTE options are available.

- [ ] **Step 2: Fix any failures**

If tests fail, check:
- Session cookies expired? Re-run `capture_options_order.py` to refresh.
- Market closed? Tests 11-14 need live option quotes. Outside market hours, expirations or quotes may not be available.
- Payload format mismatch? Compare against the captured data in `api_captures/options_order_trade_endpoints.json`.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: options order placement via HTTP API - complete"
```
