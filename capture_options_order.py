"""
Capture the API calls made when placing an options order through Fidelity's web UI.

Opens a browser, logs in (or uses saved cookies), navigates to the options
trade page, then pauses for you to manually place a 1-contract order.
All network traffic is captured and exported for endpoint discovery.

Usage:
    cd /Users/u357086/Documents/Development/git/fidelity-api
    .venv/bin/python capture_options_order.py
"""

import json
import os
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:
    from playwright_stealth import StealthConfig, stealth_sync
    HAS_STEALTH_V1 = True
except ImportError:
    try:
        from playwright_stealth import Stealth as _Stealth
        StealthConfig = None
        def stealth_sync(page, config=None):
            _Stealth().apply_stealth_sync(page)
        HAS_STEALTH_V1 = False
    except ImportError:
        HAS_STEALTH_V1 = False
        StealthConfig = None
        def stealth_sync(page, config=None):
            pass

from fidelity.network_capture import NetworkCapture

OUTPUT_DIR = "api_captures"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "options_order_flow.json")

OPTIONS_TRADE_URL = "https://digital.fidelity.com/ftgw/digital/trade-options"


def create_browser():
    """Create a headed Playwright browser with saved cookies if available."""
    pw = sync_playwright().start()

    cookie_files = sorted(
        [f for f in os.listdir('.') if f.startswith('Fidelity_') and f.endswith('.json')],
        key=lambda f: os.path.getmtime(f),
        reverse=True,
    )
    storage_state = cookie_files[0] if cookie_files else None

    if storage_state:
        print(f"  Using saved cookies: {storage_state}")
    else:
        print("  No saved cookies found - you'll need to log in manually")

    browser = pw.firefox.launch(
        headless=False,
        args=["--disable-webgl", "--disable-software-rasterizer"],
    )
    context = browser.new_context(storage_state=storage_state)
    page = context.new_page()

    if HAS_STEALTH_V1 and StealthConfig is not None:
        stealth_sync(page, StealthConfig(
            navigator_languages=False,
            navigator_user_agent=False,
            navigator_vendor=False,
        ))
    else:
        stealth_sync(page)

    return pw, browser, context, page


def wait_for_login(page):
    """If not logged in, wait for user to complete login manually."""
    page.goto("https://digital.fidelity.com/ftgw/digital/portfolio/summary", timeout=30_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass

    if "login" in page.url.lower():
        print("\n" + "=" * 60)
        print("  LOGIN REQUIRED")
        print("  Log in to your Fidelity account in the browser window.")
        print("  This script will continue once you reach the dashboard.")
        print("=" * 60 + "\n")
        try:
            page.wait_for_url("**/portfolio/**", timeout=180_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            print("  Login successful!\n")
        except PlaywrightTimeoutError:
            print("  Login timed out after 3 minutes.")
            return False
    else:
        print("  Already logged in.\n")

    return True


def save_cookies(context):
    """Save session cookies for future use."""
    state = context.storage_state()
    path = "Fidelity_capture.json"
    with open(path, "w") as f:
        json.dump(state, f)
    print(f"  Cookies saved to {path}")


def main():
    print("=" * 60)
    print("  OPTIONS ORDER CAPTURE")
    print("  Place a real 1-contract options trade while we record")
    print("  every API call the Fidelity frontend makes.")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # --- Setup ---
    pw, browser, context, page = create_browser()
    capture = NetworkCapture(page)

    try:
        # --- Login ---
        if not wait_for_login(page):
            print("  Could not log in. Exiting.")
            return 1

        save_cookies(context)

        # --- Navigate to options trade page ---
        print("\n  Navigating to options trade page...")
        capture.clear()
        capture.start()

        try:
            page.goto(OPTIONS_TRADE_URL, timeout=30_000)
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeoutError:
            print("  Warning: networkidle timeout - continuing anyway")

        page.wait_for_timeout(3000)
        print(f"  Current URL: {page.url}")
        print(f"  Page title: {page.title()}")

        # --- Pause for manual trade ---
        print("\n" + "=" * 60)
        print("  READY TO CAPTURE")
        print("=" * 60)
        print("""
  In the browser window:
    1. Select your account
    2. Search for a symbol (e.g. SPX)
    3. Select an option contract (1 contract)
    4. Fill out the order (buy/sell, limit price, etc.)
    5. Click PREVIEW ORDER
    6. WAIT - don't submit yet, let me capture the preview
    7. Then click PLACE ORDER / SUBMIT
    8. Wait for the confirmation screen

  Come back here and press Enter when done.
""")
        input("  >>> Press Enter after placing the order... ")

        # Give a moment for any final network calls
        page.wait_for_timeout(3000)
        capture.stop()

        # --- Export results ---
        api_requests = capture.get_api_requests()
        print(f"\n  Captured {len(capture.captured)} total requests")
        print(f"  API requests (JSON/POST): {len(api_requests)}")

        capture.export_json(OUTPUT_FILE)
        capture.print_summary()

        # --- Highlight the interesting endpoints ---
        print("\n" + "=" * 60)
        print("  TRADE-RELATED ENDPOINTS")
        print("=" * 60)

        trade_keywords = [
            "order", "preview", "submit", "place", "validate",
            "trade", "confirm", "execute", "ticket",
        ]

        for req in api_requests:
            path_lower = req.url_path.lower()
            if any(kw in path_lower for kw in trade_keywords):
                print(f"\n  [{req.response_status}] {req.method} {req.url_path}")
                print(f"    Auth: {req.auth_mechanism}")
                if req.post_data_json:
                    # Truncate large payloads but show structure
                    payload_str = json.dumps(req.post_data_json, indent=2)
                    if len(payload_str) > 2000:
                        payload_str = payload_str[:2000] + "\n    ... (truncated)"
                    print(f"    Request body:\n    {payload_str}")
                if req.response_json:
                    resp_str = json.dumps(req.response_json, indent=2)
                    if len(resp_str) > 2000:
                        resp_str = resp_str[:2000] + "\n    ... (truncated)"
                    print(f"    Response:\n    {resp_str}")

        # Also save a focused trade-only export
        trade_requests = [
            r for r in api_requests
            if any(kw in r.url_path.lower() for kw in trade_keywords)
        ]
        if trade_requests:
            from dataclasses import asdict
            trade_export = {
                "capture_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "description": "Options order placement flow - trade-related endpoints only",
                "total_api_requests": len(api_requests),
                "trade_requests": len(trade_requests),
                "requests": [asdict(r) for r in trade_requests],
            }
            trade_path = os.path.join(OUTPUT_DIR, "options_order_trade_endpoints.json")
            with open(trade_path, "w") as f:
                json.dump(trade_export, f, indent=2, default=str)
            print(f"\n  Trade-only export: {trade_path}")

        save_cookies(context)
        print("\n  Done! Review the captured data in api_captures/")
        return 0

    finally:
        context.close()
        browser.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
