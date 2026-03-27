# Options Order Placement via HTTP API

## Summary

Add options order placement capabilities to `FidelityAPIClient` in `fidelity/api_client.py`. Uses Fidelity's internal `trade-options` REST API endpoints discovered via network capture of a live SPX iron condor trade.

Two-phase rollout: Phase 1 adds safe read-only methods (preview, pricing, status). Phase 2 adds order execution behind a `dry_run=True` default.

## Discovered Endpoints

All endpoints use `POST` with `csrf+cookie` auth at base `https://digital.fidelity.com`.

| Key | Path | Purpose |
|---|---|---|
| `mlo_verify` | `/ftgw/digital/trade-options/api/mlo-verify` | Preview order, get confNum + cost estimates |
| `mlo_confirm` | `/ftgw/digital/trade-options/api/mlo-confirm` | Place order (same payload + confNum) |
| `mlo_verify_replace` | `/ftgw/digital/trade-options/api/mlo-verify-replace` | Preview replacement order |
| `mlo_confirm_replace` | `/ftgw/digital/trade-options/api/mlo-confirm-replace` | Execute replacement order |
| `trade_orders` | `/ftgw/digital/trade-options/api/orders` | Check order status by orderId |
| `net_debit_credit` | `/ftgw/digital/trade-options/api/net-debit-credit` | Calculate net bid/ask/mid/commissions |
| `max_gain_loss` | `/ftgw/digital/trade-options/api/max-gain-loss` | Calculate max gain/loss/breakeven |

## Auth Model

Same as existing CSRF-protected endpoints in `api_client.py`:
- Session cookies from Playwright login
- `X-CSRF-TOKEN` header from `/prgw/digital/research/api/tokens`

## Order Flow Protocol

Fidelity uses a **two-phase commit**:

1. **Verify** (`mlo-verify`): Send order details with `reqTypeCode: "N"`. Server validates and returns a `confNum` (one-time confirmation token) plus cost estimates and warnings.
2. **Confirm** (`mlo-confirm`): Send the same payload with `reqTypeCode: "P"` and the `confNum` from step 1. Server places the order.

Replace orders follow the same pattern with `mlo-verify-replace` / `mlo-confirm-replace`, adding `orderNumOrig` (the original order's confNum).

## Action Code Reference

**Request action codes** (used in `leg.action` within verify/confirm payloads):

| Code | Meaning |
|---|---|
| `BO` | Buy to Open |
| `SO` | Sell to Open |
| `BC` | Buy to Close |
| `SC` | Sell to Close |

**Response `orderActionCode` values** (returned by server — direction + option type, NOT open/close):

| Code | Meaning |
|---|---|
| `BP` | Buy Put |
| `SP` | Sell Put |
| `BC` | Buy Call |
| `SC` | Sell Call |

Note: `BC`/`SC` appear in both contexts with **different meanings**. In request payloads they mean Buy/Sell to Close. In responses they mean Buy/Sell Call. The server derives the response code from the combination of buy/sell + the option's put/call type.

**Long-form action codes** (used by `max-gain-loss` endpoint):

| Code | Meaning |
|---|---|
| `BOPEN` | Buy to Open |
| `SOPEN` | Sell to Open |
| `BCLOSE` | Buy to Close |
| `SCLOSE` | Sell to Close |

**Deriving `orderAction` for `net-debit-credit`**: This endpoint uses direction+type codes (`BP`, `SP`, `BC`, `SC`) in the `leg.orderAction` field. Parse the put/call type from the OCC symbol (the character before the strike price is `P` or `C`), then combine with the buy/sell direction from `OptionLeg.action`.

## Strategy Type Reference

| Code | Strategy |
|---|---|
| `CD` | Condor / Iron Condor |
| `SP` | Spread (vertical) |
| `ST` | Straddle |
| `SG` | Strangle |

## Data Structures

### OptionLeg dataclass

```python
@dataclass
class OptionLeg:
    symbol: str          # OCC symbol e.g. "SPXW260327P6375"
    action: str          # "BO", "SO", "BC", "SC"
    quantity: int        # number of contracts
    option_type: str = "O"  # "O" for options
```

## API Methods

### Phase 1 — Safe (no order execution)

#### `preview_option_order()`

Calls `mlo-verify` to validate an order and return cost estimates without placing it.

```python
def preview_option_order(
    self,
    legs: list[OptionLeg],
    limit_price: float,
    strategy_type: str = "CD",
    debit_credit: str = "CR",
    time_in_force: str = "D",
    acct_num: str = None,
) -> dict
```

**Request payload** (constructed by `_build_order_payload()`):
```json
{
  "orderDetails": {
    "acctNum": "Z21772945",
    "tif": "D",
    "netAmount": "4.00",
    "aonCode": false,
    "acctTypeCode": "M",
    "reqTypeCode": "N",
    "numOfLegs": "4",
    "dbCrEvenCode": "CR",
    "strategyType": "CD",
    "leg1": { "action": "BO", "type": "O", "qty": 1, "symbol": "SPXW260327P6375" },
    "leg2": { "action": "SO", "type": "O", "qty": 1, "symbol": "SPXW260327P6380" },
    "leg3": { "action": "SO", "type": "O", "qty": 1, "symbol": "SPXW260327C6390" },
    "leg4": { "action": "BO", "type": "O", "qty": 1, "symbol": "SPXW260327C6395" }
  }
}
```

**Returns**: Full verify response including `confNum`, per-leg cost estimates, net values, and warning messages.

#### `get_net_debit_credit()`

Calculates net bid/ask/mid and estimated commissions for a set of legs.

```python
def get_net_debit_credit(
    self,
    legs: list[OptionLeg],
    limit_price: float = None,
    strategy: str = "Iron Condor",
    strategy_type: str = "CD",
    debit_credit: str = "CR",
    price_type: str = "L",        # "L"=Limit, "M"=Mid
    acct_num: str = None,
) -> dict
```

**Request payload** (when `price_type="L"` with `limitPrice`):
```json
{
  "numOfLegs": "4",
  "acctNum": "Z21772945",
  "acctTypeCode": "M",
  "dbCrEvenCode": "CR",
  "strategy": "Iron Condor",
  "strategyType": "CD",
  "priceTypeCode": "L",
  "action1": "BO", "bid1": "7.8", "ask1": "8",
  "action2": "SO", "bid2": "10.2", "ask2": "10.4",
  "action3": "SO", "bid3": "7.5", "ask3": "7.6",
  "action4": "BO", "bid4": "5.9", "ask4": "6",
  "leg1": { "orderAction": "BP", "qty": 1, "symbol": "SPXW260327P6375", "optionType": "O" },
  "leg2": { "orderAction": "SP", "qty": 1, "symbol": "SPXW260327P6380", "optionType": "O" },
  "leg3": { "orderAction": "SC", "qty": 1, "symbol": "SPXW260327C6390", "optionType": "O" },
  "leg4": { "orderAction": "BC", "qty": 1, "symbol": "SPXW260327C6395", "optionType": "O" },
  "limitPrice": "4.00"
}
```

When `price_type="M"` (mid-price), `dbCrEvenCode` is set to `null` and `limitPrice` is omitted.

Note: This endpoint requires current bid/ask for each leg. The method will fetch quotes for the leg symbols automatically if not provided.

**Returns**: `{acctNum, netBid, netAsk, mid, estComm, totalCost, gcd, netDebitOrCredit}`. Note: `totalCost` is returned as a string (e.g., `"397.40"` or `"-362.60"`).

#### `get_max_gain_loss()`

Calculates max gain, max loss, and breakeven for a strategy.

```python
def get_max_gain_loss(
    self,
    legs: list[OptionLeg],
    underlying_symbol: str = ".SPX",
    strategy_type: str = "CD",
) -> dict
```

**Request payload**:
```json
{
  "underlyingSymbol": ".SPX",
  "legDetails": [
    { "symbol": "SPXW260327P6375", "orderActionCode": "BOPEN", "price": "-4.00", "qty": 1 },
    { "symbol": "SPXW260327P6380", "orderActionCode": "SOPEN", "price": "0.00", "qty": -1 },
    { "symbol": "SPXW260327C6390", "orderActionCode": "SOPEN", "price": "0.00", "qty": -1 },
    { "symbol": "SPXW260327C6395", "orderActionCode": "BOPEN", "price": "0.00", "qty": 1 }
  ],
  "strategyType": "CD"
}
```

Note: `orderActionCode` here uses long-form: `BOPEN`/`SOPEN`/`BCLOSE`/`SCLOSE`. Qty is negative for sells.

**Returns**: `{maxGain, maxLoss, maxGainNumber, maxLossNumber, breakEvenPoint, containsCloseAction}`

#### `get_order_status()`

Checks an order by its confirmation number.

```python
def get_order_status(
    self, order_id: str, acct_num: str = None,
) -> dict
```

**Request payload**:
```json
{"orderId": "C27QSJCJ", "acctNum": "Z21772945"}
```

**Returns**: `{orderDetails: [...]}` — array of per-leg dicts with `statusCode`, `statusDesc`, `decodeStatus`, `cancelableInd`, `replaceableInd`, `limitPrice`, `priceTypeCode`, `dbCrEvenCode`, `tifCode`, `strategyName`, and `orderLegInfoDetail` (symbol, qty, callPut, strikePrice, expirationDate).

### Phase 2 — Order Execution

#### `place_option_order()`

Previews and optionally places an options order.

```python
def place_option_order(
    self,
    legs: list[OptionLeg],
    limit_price: float,
    strategy_type: str = "CD",
    debit_credit: str = "CR",
    time_in_force: str = "D",
    acct_num: str = None,
    dry_run: bool = True,
) -> dict
```

Flow:
1. Calls `preview_option_order()` (mlo-verify)
2. If `dry_run=True`: returns the preview result (no order placed)
3. If `dry_run=False`: extracts `confNum` from preview, sends to `mlo-confirm` with `reqTypeCode: "P"`

**Returns**: Preview result (if dry_run) or confirmation result (if live).

#### `replace_option_order()`

Replaces an existing open order with new terms.

```python
def replace_option_order(
    self,
    original_order_id: str,
    legs: list[OptionLeg],
    limit_price: float,
    strategy_type: str = "CD",
    debit_credit: str = "CR",
    time_in_force: str = "D",
    acct_num: str = None,
    dry_run: bool = True,
) -> dict
```

Flow:
1. Calls `mlo-verify-replace` with `orderNumOrig` set to original order's confNum
2. If `dry_run=True`: returns preview only
3. If `dry_run=False`: sends to `mlo-confirm-replace` (also includes `orderNumOrig`)

### Private Helper

#### `_build_order_payload()`

Constructs the `orderDetails` dict shared by verify/confirm/replace.

```python
def _build_order_payload(
    self,
    legs: list[OptionLeg],
    limit_price: float,
    strategy_type: str,
    debit_credit: str,
    time_in_force: str,
    req_type_code: str,       # "N" for verify, "P" for confirm
    acct_num: str = None,
    conf_num: str = None,     # From verify response, required for confirm
    original_order_id: str = None,  # For replace orders
) -> dict
```

## Implementation Notes

- All new methods use `self._csrf_headers()` for auth, same as existing `get_balances()` / `get_positions()`.
- Trading methods must override the `Referer` header to `https://digital.fidelity.com/ftgw/digital/trade-options` (the default in `DEFAULT_HEADERS` points to options-research).
- `acctTypeCode` is derived from `AccountInfo`: `"M"` for margin accounts, `"C"` for cash.
- Leg symbols use the format without the leading dash (e.g., `SPXW260327P6375`). The API response adds the dash prefix in `complexOrderDetails`.
- `netAmount` is sent as a string with 2 decimal places (e.g., `"4.00"`).
- `numOfLegs` is sent as a string (e.g., `"4"`).
- `qty` in verify/confirm legs is an int, but in replace payloads it's a string. `_build_order_payload()` must check `original_order_id is not None` and stringify qty accordingly.
- Error handling: check HTTP status codes and the `messages` array in verify/confirm responses. Messages with `type: "error"` indicate failures (insufficient buying power, invalid symbols, market closed). Warnings (`type: "warning"`) are informational (e.g., last day to trade).

## File Changes

Only `fidelity/api_client.py` is modified:
- Add 7 new endpoint entries to `ENDPOINTS` dict
- Add `OptionLeg` dataclass
- Add 6 public methods + 1 private helper to `FidelityAPIClient`
- Action code mapping constants (`ACTION_LONG_FORM`, `ACTION_ORDER_CODE`)

No new files created. No changes to `fidelity.py`, `network_capture.py`, or `__init__.py`.

## Known Gaps / Future Work

- **`cancel_option_order()`**: The captured `orders` response includes `cancelableInd: true`, but no cancel endpoint was captured. Likely exists at a similar path (e.g., `mlo-cancel`). Requires a separate capture session.
- **Single-leg options orders**: This spec covers multi-leg orders (MLO). Single-leg options orders may use different endpoints (e.g., `slo-verify`/`slo-confirm`).
- **Automatic session re-auth**: No handling for expired sessions during order flow.

## Testing Strategy

- Phase 1 methods can be tested against a live session with zero risk (read-only)
- Phase 2 `place_option_order()` defaults to `dry_run=True` which only calls `mlo-verify`
- Live order placement (`dry_run=False`) requires explicit opt-in
- Extend existing `test_api_client.py` with new test functions
