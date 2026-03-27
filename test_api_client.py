"""
Quick validation of the FidelityAPIClient against a live session.

Usage:
    cd /Users/u357086/Documents/Development/git/fidelity-api
    .venv/bin/python test_api_client.py

Requires saved cookies from a previous capture_api.py or login session.
"""

import json
import os
import sys
import time

from fidelity.api_client import FidelityAPIClient


def find_cookies_file():
    """Find the most recent Fidelity cookies file."""
    candidates = sorted(
        [f for f in os.listdir('.') if f.startswith('Fidelity_') and f.endswith('.json')],
        key=lambda f: os.path.getmtime(f),
        reverse=True,
    )
    return candidates[0] if candidates else None


def test_session(client: FidelityAPIClient):
    """Test 1: Verify session is valid."""
    print("\n--- Test: Session Validity ---")
    valid = client.is_session_valid()
    print(f"  Session valid: {valid}")
    return valid


def test_quotes(client: FidelityAPIClient):
    """Test 2: Get SPX and VIX quotes."""
    print("\n--- Test: Quotes ---")
    try:
        quotes = client.get_quotes([".SPX", ".VIX"])
        for sym, qd in quotes.items():
            price = qd.get("lastPrice", "N/A")
            change = qd.get("netChgToday", "N/A")
            pct = qd.get("pctChgToday", "N/A")
            print(f"  {sym}: ${price} ({change}, {pct}%)")
        return bool(quotes)
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def test_expirations(client: FidelityAPIClient):
    """Test 3: Get option expirations."""
    print("\n--- Test: Option Expirations ---")
    try:
        exps = client.get_option_expirations(".SPX")
        print(f"  Found {len(exps)} expirations")
        for exp in exps[:5]:
            print(f"    {exp.get('date', '?')} (DTE={exp.get('daysToExpiration', '?')}, type={exp.get('optionPeriodicity', '?')})")
        dte0 = client.get_0dte_expiration(".SPX")
        print(f"  0DTE expiration: {dte0}")
        return bool(exps)
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def test_option_chain(client: FidelityAPIClient):
    """Test 4: Get option chain (the critical one)."""
    print("\n--- Test: Option Chain ---")
    try:
        chain = client.get_option_chain_parsed("SPX")
        print(f"  Strikes returned: {len(chain)}")
        if chain:
            # Find ATM
            spx = client.get_spx_price()
            if spx:
                atm = min(chain, key=lambda x: abs((x["strike"] or 0) - spx))
                print(f"\n  ATM strike ({atm['strike']}) at SPX={spx}:")
                print(f"    Call: bid={atm['call_bid']} ask={atm['call_ask']} delta={atm['call_delta']} iv={atm['call_iv']}")
                print(f"    Put:  bid={atm['put_bid']} ask={atm['put_ask']} delta={atm['put_delta']} iv={atm['put_iv']}")
                print(f"    Call symbol: {atm['call_symbol']}")
                print(f"    Put symbol:  {atm['put_symbol']}")

            # Show range
            strikes = [r["strike"] for r in chain if r["strike"]]
            print(f"\n  Strike range: {min(strikes)} - {max(strikes)}")
            # Count with valid Greeks
            with_delta = [r for r in chain if r["call_delta"] is not None and r["call_delta"] != 0]
            print(f"  Strikes with call delta: {len(with_delta)}/{len(chain)}")

        return len(chain) > 0
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_volatility(client: FidelityAPIClient):
    """Test 5: Get volatility data."""
    print("\n--- Test: Volatility ---")
    try:
        vol = client.get_volatility("SPX")
        print(f"  HV30: {vol.get('hv30', 'N/A')}")
        print(f"  IV30: {vol.get('iv30', 'N/A')}")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def test_csrf_token(client: FidelityAPIClient):
    """Test 6: Get CSRF token."""
    print("\n--- Test: CSRF Token ---")
    try:
        token = client.get_csrf_token()
        print(f"  Token: {token[:20]}..." if token else "  Token: None")
        return bool(token)
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def test_accounts(client: FidelityAPIClient):
    """Test 7: Discover accounts."""
    print("\n--- Test: Account Discovery ---")
    try:
        accounts = client.discover_accounts()
        print(f"  Found {len(accounts)} accounts")
        for acct in accounts:
            opt_str = f"option_level={acct.option_level}" if acct.is_option else "no options"
            margin_str = "margin" if acct.is_margin else "cash"
            print(f"    {acct.acct_num}: {acct.acct_type}/{acct.acct_sub_type} ({margin_str}, {opt_str})")
        return bool(accounts)
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_balances(client: FidelityAPIClient):
    """Test 8: Get account balances."""
    print("\n--- Test: Account Balances ---")
    try:
        bal = client.get_balances()
        print(f"  Account: {bal.get('acctNum', 'N/A')}")
        print(f"  Total Value:     ${bal.get('totalAcctVal', 'N/A'):>12}")
        print(f"  Cash Available:  ${bal.get('cashAvailForTrade', 'N/A'):>12}")
        print(f"  Margin BP:       ${bal.get('mrgnBP', 'N/A'):>12}")
        print(f"  Intraday BP:     ${bal.get('intraDayBP', 'N/A'):>12}")
        print(f"  Is Margin:       {bal.get('isMrgnAcct', 'N/A')}")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_positions(client: FidelityAPIClient):
    """Test 9: Get option positions."""
    print("\n--- Test: Option Positions ---")
    try:
        options = client.get_option_positions()
        print(f"  Option positions: {len(options)}")
        for pos in options[:5]:
            desc = pos.get("securityDescription", "?")
            qty = pos.get("intradayTradeDateShares", "?")
            pnl = pos.get("totalGainLoss", "?")
            print(f"    {desc}: qty={qty}, P&L=${pnl}")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_ic_chain(client: FidelityAPIClient):
    """Test 10: Get iron condor chain data."""
    print("\n--- Test: IC Chain Data ---")
    try:
        chain = client.get_ic_chain_data(otm_range=150)
        spx = client.get_spx_price()
        print(f"  SPX: {spx}")
        print(f"  Strikes in range (±150 pts): {len(chain)}")

        # Find put spread candidates (20-40 pts OTM)
        if spx:
            put_cands = [
                r for r in chain
                if r["strike"] and r["put_bid"]
                and (spx - 40) <= r["strike"] <= (spx - 20)
                and r["put_bid"] > 0
            ]
            call_cands = [
                r for r in chain
                if r["strike"] and r["call_bid"]
                and (spx + 20) <= r["strike"] <= (spx + 40)
                and r["call_bid"] > 0
            ]
            print(f"  Put candidates (20-40 OTM): {len(put_cands)}")
            print(f"  Call candidates (20-40 OTM): {len(call_cands)}")

            if put_cands:
                p = put_cands[0]
                print(f"\n  Sample put @ {p['strike']}: bid={p['put_bid']} ask={p['put_ask']} delta={p['put_delta']}")
            if call_cands:
                c = call_cands[0]
                print(f"  Sample call @ {c['strike']}: bid={c['call_bid']} ask={c['call_ask']} delta={c['call_delta']}")

        return len(chain) > 0
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


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


def main():
    print("=" * 60)
    print("  FIDELITY API CLIENT VALIDATION")
    print("=" * 60)

    # Find cookies
    cookies_file = find_cookies_file()
    if not cookies_file:
        print("\nNo saved cookies found. Run capture_api.py first to log in.")
        sys.exit(1)

    print(f"\nUsing cookies: {cookies_file}")

    # Create client
    client = FidelityAPIClient.from_storage_state(cookies_file)

    # Run tests
    results = {}
    tests = [
        ("session", test_session),
        ("quotes", test_quotes),
        ("expirations", test_expirations),
        ("option_chain", test_option_chain),
        ("volatility", test_volatility),
        ("csrf_token", test_csrf_token),
        ("accounts", test_accounts),
        ("balances", test_balances),
        ("positions", test_positions),
        ("ic_chain", test_ic_chain),
        ("preview_order", test_preview_option_order),
        ("net_debit_credit", test_net_debit_credit),
        ("max_gain_loss", test_max_gain_loss),
        ("place_order_dry", test_place_option_order_dry_run),
    ]

    for name, test_fn in tests:
        try:
            results[name] = test_fn(client)
        except Exception as e:
            results[name] = False
            print(f"  UNEXPECTED ERROR: {e}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  {passed}/{total} tests passed")
    print(f"{'='*60}\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
