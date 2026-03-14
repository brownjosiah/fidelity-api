"""
Fidelity API Discovery Script

Captures all XHR/fetch API calls made by Fidelity's frontend when loading
options chain, quote, account, and trade ticket pages. Uses Playwright
network interception to discover internal REST endpoints that can be
called directly via HTTP (bypassing DOM automation).

Usage:
    cd /Users/u357086/Documents/Development/git/fidelity-api
    .venv/bin/python capture_api.py

    # With specific scenarios
    .venv/bin/python capture_api.py --scenarios options,quotes

    # Skip login (use saved cookies)
    .venv/bin/python capture_api.py --skip-login

    # Interactive mode (pauses between scenarios for manual exploration)
    .venv/bin/python capture_api.py --interactive
"""

import argparse
import json
import os
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

try:
    # v1 API
    from playwright_stealth import StealthConfig, stealth_sync
    HAS_STEALTH = True
except ImportError:
    try:
        # v2 API
        from playwright_stealth import Stealth as _Stealth
        StealthConfig = None
        def stealth_sync(page, config=None):
            _Stealth().apply_stealth_sync(page)
        HAS_STEALTH = True
    except ImportError:
        HAS_STEALTH = False

from fidelity.network_capture import NetworkCapture


# --- Fidelity URLs ---

URLS = {
    "login": "https://digital.fidelity.com/prgw/digital/login/full-page",
    "summary": "https://digital.fidelity.com/ftgw/digital/portfolio/summary",
    "positions": "https://digital.fidelity.com/ftgw/digital/portfolio/positions",
    "options_research": "https://digital.fidelity.com/ftgw/digital/options-research/?symbol=SPX",
    "quote_dashboard": "https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=SPX",
    "equity_trade": "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry",
}

OUTPUT_DIR = "api_captures"


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def create_browser():
    """Create a Playwright browser matching FidelityAutomation's config."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()

    # Find saved cookies
    cookie_files = sorted(
        [f for f in os.listdir('.') if f.startswith('Fidelity_') and f.endswith('.json')],
        key=lambda f: os.path.getmtime(f),
        reverse=True,
    )
    storage_state = cookie_files[0] if cookie_files else None

    if storage_state:
        print(f"  Using saved cookies: {storage_state}")
    else:
        print("  No saved cookies found — you'll need to log in")

    # Launch Firefox (matching FidelityAutomation)
    browser = pw.firefox.launch(
        headless=False,
        args=["--disable-webgl", "--disable-software-rasterizer"],
    )

    context = browser.new_context(
        storage_state=storage_state
    )
    page = context.new_page()

    # Apply stealth
    if HAS_STEALTH:
        if StealthConfig is not None:
            config = StealthConfig(
                navigator_languages=False,
                navigator_user_agent=False,
                navigator_vendor=False,
            )
            stealth_sync(page, config)
        else:
            stealth_sync(page)

    return pw, browser, context, page


def wait_for_login(page):
    """If we land on the login page, wait for user to complete login manually."""
    if "login" in page.url.lower():
        print("\n" + "=" * 60)
        print("  LOGIN REQUIRED")
        print("  Please log in to your Fidelity account in the browser.")
        print("  The script will continue once you reach the dashboard.")
        print("=" * 60 + "\n")
        try:
            page.wait_for_url("**/portfolio/**", timeout=180_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            print("  Login successful!\n")
            return True
        except PlaywrightTimeoutError:
            print("  Login timed out after 3 minutes.")
            return False
    return True


def safe_navigate(page, url, label, wait_extra=3.0):
    """Navigate to a URL and wait for it to load."""
    print(f"\n  Navigating to {label}...")
    print(f"  URL: {url}")
    try:
        page.goto(url, timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeoutError:
        print(f"  Warning: networkidle timeout on {label} — continuing anyway")

    # Extra wait for dynamic JS rendering
    if wait_extra > 0:
        page.wait_for_timeout(int(wait_extra * 1000))

    print(f"  Page loaded: {page.title()}")
    return True


# --- Capture Scenarios ---

def capture_options_chain(page, capture: NetworkCapture, interactive: bool = False):
    """
    Scenario: Load the SPX options research page and capture all API calls.
    This is the most important scenario — we need to find the option chain
    data endpoint with strikes, bid/ask, Greeks.
    """
    print("\n" + "=" * 60)
    print("  SCENARIO: OPTIONS CHAIN")
    print("=" * 60)

    capture.clear()
    capture.start()

    safe_navigate(page, URLS["options_research"], "Options Research (SPX)")

    # Try to interact with the page to trigger additional API calls
    print("\n  Exploring options chain page...")

    # 1. Look for expiration date tabs/selectors and click through them
    try:
        # Common patterns for expiration selectors
        exp_selectors = [
            "[data-testid*='expir']",
            "[class*='expir']",
            "[aria-label*='expir']",
            "[class*='date-tab']",
            "button:has-text('Weekly')",
            "button:has-text('Monthly')",
        ]
        for sel in exp_selectors:
            elements = page.locator(sel).all()
            if elements:
                print(f"  Found {len(elements)} expiration elements with '{sel}'")
                # Click the first one to trigger data load
                if len(elements) > 1:
                    try:
                        elements[1].click(timeout=3000)
                        page.wait_for_timeout(2000)
                        print(f"    Clicked alternate expiration — captured additional API calls")
                    except Exception:
                        pass
                break
    except Exception as e:
        print(f"  Note: Could not find expiration selectors ({e})")

    # 2. Look for Greeks toggle/checkbox
    try:
        greek_toggles = page.locator(
            "button:has-text('Greeks'), "
            "[aria-label*='Greek'], "
            "input[type='checkbox']:near(:text('Greeks')), "
            "[class*='greek']"
        ).all()
        if greek_toggles:
            print(f"  Found {len(greek_toggles)} Greek toggle elements")
            try:
                greek_toggles[0].click(timeout=3000)
                page.wait_for_timeout(2000)
                print("    Toggled Greeks — captured additional API calls")
            except Exception:
                pass
    except Exception:
        pass

    # 3. Look for "Show All" or pagination to load more strikes
    try:
        show_all = page.locator(
            "button:has-text('Show All'), "
            "button:has-text('View All'), "
            "a:has-text('Show All'), "
            "[class*='show-all']"
        ).all()
        if show_all:
            print(f"  Found 'Show All' button")
            try:
                show_all[0].click(timeout=3000)
                page.wait_for_timeout(3000)
                print("    Expanded full chain — captured additional API calls")
            except Exception:
                pass
    except Exception:
        pass

    # 4. Try switching to a different symbol (VIX) to see if the API call pattern changes
    try:
        symbol_input = page.locator(
            "input[placeholder*='Symbol'], "
            "input[aria-label*='Symbol'], "
            "input[name*='symbol'], "
            "#symbolInput"
        ).first
        if symbol_input.is_visible(timeout=2000):
            print("  Trying VIX symbol to compare API patterns...")
            symbol_input.fill("VIX")
            symbol_input.press("Enter")
            page.wait_for_timeout(3000)
            # Switch back to SPX
            symbol_input.fill("SPX")
            symbol_input.press("Enter")
            page.wait_for_timeout(3000)
            print("    Captured API calls for VIX + SPX")
    except Exception:
        pass

    if interactive:
        print("\n  >>> INTERACTIVE MODE: Explore the options chain page manually.")
        print("      Network capture is active. All API calls are being recorded.")
        input("      Press Enter when done exploring... ")

    capture.stop()

    # Analyze
    api_requests = capture.get_api_requests()
    print(f"\n  Captured {len(capture.captured)} total requests, {len(api_requests)} API calls")

    # Save scenario results
    ensure_output_dir()
    capture.export_json(f"{OUTPUT_DIR}/options_chain.json")
    capture.print_summary()

    # Look for option chain data specifically
    chain_candidates = []
    for req in api_requests:
        if req.response_json:
            body = req.response_json
            # Look for common option chain response patterns
            body_str = json.dumps(body).lower()
            if any(kw in body_str for kw in ["strike", "bid", "ask", "delta", "optionchain", "option_chain", "puts", "calls"]):
                chain_candidates.append(req)

    if chain_candidates:
        print(f"\n  *** FOUND {len(chain_candidates)} OPTION CHAIN ENDPOINT CANDIDATE(S)! ***")
        for c in chain_candidates:
            print(f"      {c.method} {c.url_path}")
            if c.response_json and isinstance(c.response_json, dict):
                print(f"      Top-level keys: {list(c.response_json.keys())[:10]}")
    else:
        print("\n  No obvious option chain endpoints found in JSON responses.")
        print("  The data may be embedded in HTML or use a non-standard format.")

    return capture.captured.copy()


def capture_quotes(page, capture: NetworkCapture, interactive: bool = False):
    """
    Scenario: Load quote pages for SPX and VIX to find pricing endpoints.
    """
    print("\n" + "=" * 60)
    print("  SCENARIO: QUOTES (SPX + VIX)")
    print("=" * 60)

    capture.clear()
    capture.start()

    safe_navigate(page, URLS["quote_dashboard"], "Quote Dashboard (SPX)")

    # Also try VIX
    vix_url = URLS["quote_dashboard"].replace("symbol=SPX", "symbol=VIX")
    safe_navigate(page, vix_url, "Quote Dashboard (VIX)")

    if interactive:
        print("\n  >>> INTERACTIVE MODE: Explore quote pages manually.")
        input("      Press Enter when done exploring... ")

    capture.stop()

    api_requests = capture.get_api_requests()
    print(f"\n  Captured {len(capture.captured)} total requests, {len(api_requests)} API calls")

    ensure_output_dir()
    capture.export_json(f"{OUTPUT_DIR}/quotes.json")
    capture.print_summary()

    # Look for quote data
    quote_candidates = []
    for req in api_requests:
        if req.response_json:
            body_str = json.dumps(req.response_json).lower()
            if any(kw in body_str for kw in ["lastprice", "last_price", "bidprice", "askprice", "quote", "price"]):
                quote_candidates.append(req)

    if quote_candidates:
        print(f"\n  *** FOUND {len(quote_candidates)} QUOTE ENDPOINT CANDIDATE(S)! ***")
        for c in quote_candidates:
            print(f"      {c.method} {c.url_path}")
    else:
        print("\n  No obvious quote endpoints found in JSON responses.")

    return capture.captured.copy()


def capture_account(page, capture: NetworkCapture, interactive: bool = False):
    """
    Scenario: Load portfolio/account pages to find position and balance endpoints.
    """
    print("\n" + "=" * 60)
    print("  SCENARIO: ACCOUNT & POSITIONS")
    print("=" * 60)

    capture.clear()
    capture.start()

    safe_navigate(page, URLS["summary"], "Portfolio Summary")
    safe_navigate(page, URLS["positions"], "Portfolio Positions")

    if interactive:
        print("\n  >>> INTERACTIVE MODE: Explore account pages manually.")
        input("      Press Enter when done exploring... ")

    capture.stop()

    api_requests = capture.get_api_requests()
    print(f"\n  Captured {len(capture.captured)} total requests, {len(api_requests)} API calls")

    ensure_output_dir()
    capture.export_json(f"{OUTPUT_DIR}/account.json")
    capture.print_summary()

    # Look for account/position data
    acct_candidates = []
    for req in api_requests:
        if req.response_json:
            body_str = json.dumps(req.response_json).lower()
            if any(kw in body_str for kw in ["account", "balance", "position", "holding", "netliq", "buying_power", "buyingpower"]):
                acct_candidates.append(req)

    if acct_candidates:
        print(f"\n  *** FOUND {len(acct_candidates)} ACCOUNT ENDPOINT CANDIDATE(S)! ***")
        for c in acct_candidates:
            print(f"      {c.method} {c.url_path}")
    else:
        print("\n  No obvious account endpoints found in JSON responses.")

    return capture.captured.copy()


def capture_trade_ticket(page, capture: NetworkCapture, interactive: bool = False):
    """
    Scenario: Navigate to options trade pages and discover trade-related endpoints.
    Does NOT place any orders — only observes the trade ticket setup flow.
    """
    print("\n" + "=" * 60)
    print("  SCENARIO: TRADE TICKET DISCOVERY")
    print("=" * 60)

    capture.clear()
    capture.start()

    # Start from the options research page to find trade links
    safe_navigate(page, URLS["options_research"], "Options Research (SPX)")

    # Try to find and click a trade/buy link on the options page
    trade_url_found = None
    try:
        trade_links = page.locator(
            "a:has-text('Trade'), "
            "button:has-text('Trade'), "
            "a:has-text('Buy'), "
            "a:has-text('Sell'), "
            "[aria-label*='trade'], "
            "[aria-label*='Trade']"
        ).all()
        if trade_links:
            print(f"  Found {len(trade_links)} trade link(s) on options page")
            for i, link in enumerate(trade_links[:3]):
                text = link.text_content().strip()[:50]
                href = link.get_attribute("href") or "(no href)"
                print(f"    [{i}] text=\"{text}\" href=\"{href}\"")

            # Click the first trade link
            try:
                trade_links[0].click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=15_000)
                page.wait_for_timeout(3000)
                trade_url_found = page.url
                print(f"\n  Trade ticket URL: {trade_url_found}")
            except Exception as e:
                print(f"  Could not click trade link: {e}")
    except Exception as e:
        print(f"  Could not find trade links: {e}")

    # Also try known/guessed options trade URLs
    options_trade_urls = [
        "https://digital.fidelity.com/ftgw/digital/trade-options",
        "https://digital.fidelity.com/ftgw/digital/trade-options/index/orderEntry",
        "https://digital.fidelity.com/ftgw/digital/options-trade",
    ]
    for url in options_trade_urls:
        if trade_url_found and url in trade_url_found:
            continue
        try:
            print(f"\n  Trying: {url}")
            page.goto(url, timeout=10_000)
            page.wait_for_load_state("networkidle", timeout=10_000)
            page.wait_for_timeout(2000)
            if page.url != url and "error" not in page.url.lower():
                print(f"  Redirected to: {page.url}")
            print(f"  Page title: {page.title()}")
        except Exception as e:
            print(f"  Failed: {e}")

    # Look for multi-leg/strategy trade options
    try:
        strategy_links = page.locator(
            "a:has-text('Strategy'), "
            "button:has-text('Strategy'), "
            "a:has-text('Multi-Leg'), "
            "button:has-text('Multi-Leg'), "
            "a:has-text('Spread'), "
            "button:has-text('Spread'), "
            "[aria-label*='strategy'], "
            "[aria-label*='multi']"
        ).all()
        if strategy_links:
            print(f"\n  Found {len(strategy_links)} strategy/multi-leg link(s)")
            for link in strategy_links[:5]:
                text = link.text_content().strip()[:50]
                print(f"    text=\"{text}\"")
    except Exception:
        pass

    if interactive:
        print("\n  >>> INTERACTIVE MODE: Explore the trade ticket manually.")
        print("      Try navigating to the multi-leg options order page.")
        print("      DO NOT PLACE ANY ORDERS.")
        input("      Press Enter when done exploring... ")

    capture.stop()

    api_requests = capture.get_api_requests()
    print(f"\n  Captured {len(capture.captured)} total requests, {len(api_requests)} API calls")

    ensure_output_dir()
    capture.export_json(f"{OUTPUT_DIR}/trade_ticket.json")
    capture.print_summary()

    return capture.captured.copy()


# --- Main ---

def run_analysis(all_captured: list):
    """Run aggregate analysis across all captured scenarios."""
    print("\n" + "=" * 70)
    print("  AGGREGATE API ANALYSIS")
    print("=" * 70)

    # Deduplicate by URL path
    unique_paths = {}
    for req in all_captured:
        path = req.url_path
        if path not in unique_paths:
            unique_paths[path] = req
        elif req.has_json_response and not unique_paths[path].has_json_response:
            unique_paths[path] = req

    # Classify endpoints
    api_endpoints = []
    html_pages = []
    other = []
    for path, req in unique_paths.items():
        if req.has_json_response or req.method in ("POST", "PUT", "PATCH", "DELETE"):
            api_endpoints.append(req)
        elif "html" in req.content_type:
            html_pages.append(req)
        else:
            other.append(req)

    print(f"\n  Unique URL paths: {len(unique_paths)}")
    print(f"  API endpoints (JSON): {len(api_endpoints)}")
    print(f"  HTML pages: {len(html_pages)}")
    print(f"  Other: {len(other)}")

    # Print all API endpoints sorted by path
    if api_endpoints:
        print(f"\n  {'─'*65}")
        print(f"  ALL DISCOVERED API ENDPOINTS")
        print(f"  {'─'*65}")
        for req in sorted(api_endpoints, key=lambda r: r.url_path):
            status = req.response_status or "???"
            method = req.method
            path = req.url_path
            auth = req.auth_mechanism
            response_size = len(req.response_body) if req.response_body else 0
            keys = ""
            if req.response_json and isinstance(req.response_json, dict):
                keys = f" keys={list(req.response_json.keys())[:5]}"
            print(f"\n    [{status}] {method} {path}")
            print(f"         auth: {auth} | size: {response_size:,}B{keys}")

    # Auth summary
    auth_mechs = set()
    csrf_tokens = set()
    for req in all_captured:
        if req.auth_mechanism:
            auth_mechs.add(req.auth_mechanism)
        for header in ("x-csrf-token", "x-xsrf-token"):
            if header in req.request_headers:
                csrf_tokens.add(req.request_headers[header][:20] + "...")

    print(f"\n  {'─'*65}")
    print(f"  AUTHENTICATION SUMMARY")
    print(f"  {'─'*65}")
    print(f"  Mechanisms seen: {', '.join(auth_mechs)}")
    if csrf_tokens:
        print(f"  CSRF tokens: {csrf_tokens}")
    print(f"\n  Recommendation:")
    if "bearer" in str(auth_mechs):
        print("    → Bearer token auth detected. Extract from browser session for HTTP client.")
    elif csrf_tokens:
        print("    → CSRF token auth detected. Must include X-CSRF-Token header in direct calls.")
    else:
        print("    → Cookie-based auth only. Extract cookies from Playwright for HTTP client.")

    # Save aggregate results
    ensure_output_dir()
    aggregate = {
        "capture_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_unique_paths": len(unique_paths),
        "api_endpoint_count": len(api_endpoints),
        "auth_mechanisms": list(auth_mechs),
        "csrf_tokens_found": len(csrf_tokens),
        "api_endpoints": [
            {
                "method": r.method,
                "url": r.url,
                "path": r.url_path,
                "status": r.response_status,
                "auth": r.auth_mechanism,
                "content_type": r.content_type,
                "response_size": len(r.response_body) if r.response_body else 0,
                "response_keys": list(r.response_json.keys())[:10] if r.response_json and isinstance(r.response_json, dict) else None,
                "has_json": r.has_json_response,
            }
            for r in sorted(api_endpoints, key=lambda r: r.url_path)
        ],
    }
    with open(f"{OUTPUT_DIR}/aggregate_analysis.json", "w") as f:
        json.dump(aggregate, f, indent=2)
    print(f"\n  Aggregate analysis saved to {OUTPUT_DIR}/aggregate_analysis.json")

    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Fidelity API Discovery Script")
    parser.add_argument(
        "--scenarios",
        default="options,quotes,account,trade",
        help="Comma-separated scenarios to run: options,quotes,account,trade (default: all)",
    )
    parser.add_argument(
        "--skip-login",
        action="store_true",
        help="Skip login check (assumes saved cookies are valid)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pause between scenarios for manual exploration",
    )
    args = parser.parse_args()

    scenarios = [s.strip() for s in args.scenarios.split(",")]

    print("\n" + "=" * 70)
    print("  FIDELITY API DISCOVERY")
    print("  Capturing internal REST/XHR endpoints via network interception")
    print("=" * 70)
    print(f"\n  Scenarios: {', '.join(scenarios)}")
    print(f"  Interactive: {args.interactive}")
    print(f"  Output dir: {OUTPUT_DIR}/")

    # Create browser
    print("\n  Starting browser...")
    pw, browser, context, page = create_browser()

    # Create capture instance
    capture = NetworkCapture(page)

    try:
        # Check login
        if not args.skip_login:
            safe_navigate(page, URLS["summary"], "Portfolio Summary (login check)")
            if not wait_for_login(page):
                print("  Failed to log in. Exiting.")
                return

        # Save cookies after successful login
        context.storage_state(path="Fidelity_capture.json")
        print("  Cookies saved to Fidelity_capture.json")

        # Run selected scenarios
        all_captured = []

        scenario_map = {
            "options": lambda: capture_options_chain(page, capture, args.interactive),
            "quotes": lambda: capture_quotes(page, capture, args.interactive),
            "account": lambda: capture_account(page, capture, args.interactive),
            "trade": lambda: capture_trade_ticket(page, capture, args.interactive),
        }

        for scenario in scenarios:
            if scenario in scenario_map:
                results = scenario_map[scenario]()
                all_captured.extend(results)
            else:
                print(f"\n  Unknown scenario: {scenario}")

        # Run aggregate analysis
        if all_captured:
            run_analysis(all_captured)

        print("\n  All scenarios complete!")
        print(f"  Results saved to {OUTPUT_DIR}/")
        print(f"  Files:")
        if os.path.exists(OUTPUT_DIR):
            for f in sorted(os.listdir(OUTPUT_DIR)):
                size = os.path.getsize(os.path.join(OUTPUT_DIR, f))
                print(f"    {f} ({size:,} bytes)")

        input("\n  Press Enter to close browser...")

    except KeyboardInterrupt:
        print("\n\n  Interrupted. Saving partial results...")
        if capture.captured:
            ensure_output_dir()
            capture.export_json(f"{OUTPUT_DIR}/partial_capture.json", api_only=False)
    except Exception as e:
        print(f"\n  Error: {e}")
        import traceback
        traceback.print_exc()
        if capture.captured:
            ensure_output_dir()
            capture.export_json(f"{OUTPUT_DIR}/error_capture.json", api_only=False)
        input("\n  Press Enter to close browser...")
    finally:
        context.close()
        browser.close()
        pw.stop()


if __name__ == "__main__":
    main()
