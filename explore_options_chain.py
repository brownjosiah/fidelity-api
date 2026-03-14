"""
Playwright automation script to explore Fidelity's options chain page.
Launches a real browser, navigates to the options chain, and dumps
all DOM selectors needed for automation.

Usage:
    cd /Users/u357086/Documents/Development/git/fidelity-api
    .venv/bin/python explore_options_chain.py
"""

import json
import time
import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# playwright-stealth v2 API
try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

OPTIONS_URL = "https://digital.fidelity.com/ftgw/digital/options-research/?symbol=SPX"
OUTPUT_FILE = "options_chain_selectors.json"


def explore_options_chain(page):
    """Extract all relevant selectors from the options chain page."""

    results = {}

    # 1. Page metadata
    results["page_info"] = {
        "title": page.title(),
        "url": page.url,
    }

    # 2. All tables (options chain is table-based)
    results["tables"] = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('table')).map((t, idx) => {
            const headers = Array.from(t.querySelectorAll('th')).map(th => ({
                text: th.textContent?.trim(),
                class: th.className?.substring(0, 120),
                id: th.id,
                colspan: th.colSpan,
                dataAttrs: Object.fromEntries(
                    Array.from(th.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                )
            }));
            const firstRow = t.querySelector('tbody tr');
            const firstRowCells = firstRow ? Array.from(firstRow.querySelectorAll('td')).map(td => ({
                text: td.textContent?.trim().substring(0, 80),
                class: td.className?.substring(0, 120),
                dataAttrs: Object.fromEntries(
                    Array.from(td.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                )
            })) : [];
            return {
                index: idx,
                id: t.id,
                class: t.className?.substring(0, 150),
                headerCount: headers.length,
                headers: headers,
                rowCount: t.querySelectorAll('tbody tr').length,
                firstRowCells: firstRowCells.slice(0, 30),
                dataAttrs: Object.fromEntries(
                    Array.from(t.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                )
            };
        });
    }""")

    # 3. Expiration date tabs/selectors
    results["expiration_selectors"] = page.evaluate("""() => {
        const items = [];
        // Look for tabs, dropdowns, or date-related selectors
        document.querySelectorAll('[class*="expir"], [class*="Expir"], [data-testid*="expir"], [aria-label*="expir"], [class*="date"], [class*="Date"], select, [role="tab"], [role="tablist"]').forEach(el => {
            items.push({
                tag: el.tagName,
                id: el.id,
                class: el.className?.substring(0, 150),
                text: el.textContent?.trim().substring(0, 200),
                role: el.getAttribute('role'),
                ariaLabel: el.getAttribute('aria-label'),
                dataAttrs: Object.fromEntries(
                    Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                )
            });
        });
        return items;
    }""")

    # 4. Option chain rows - look for rows with strike prices, bid/ask
    results["chain_row_structure"] = page.evaluate("""() => {
        // Get all table rows in the main content area
        const rows = document.querySelectorAll('table tbody tr');
        const sampleRows = [];
        for (let i = 0; i < Math.min(5, rows.length); i++) {
            const cells = Array.from(rows[i].querySelectorAll('td')).map((td, cellIdx) => ({
                index: cellIdx,
                text: td.textContent?.trim().substring(0, 80),
                class: td.className?.substring(0, 120),
                innerHTML: td.innerHTML?.substring(0, 300),
                dataAttrs: Object.fromEntries(
                    Array.from(td.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                ),
                links: Array.from(td.querySelectorAll('a')).map(a => ({
                    text: a.textContent?.trim(),
                    href: a.href,
                    class: a.className?.substring(0, 80),
                    ariaLabel: a.getAttribute('aria-label')
                })),
                buttons: Array.from(td.querySelectorAll('button')).map(b => ({
                    text: b.textContent?.trim(),
                    class: b.className?.substring(0, 80),
                    ariaLabel: b.getAttribute('aria-label')
                }))
            }));
            sampleRows.push({
                rowIndex: i,
                class: rows[i].className?.substring(0, 120),
                cellCount: cells.length,
                cells: cells,
                dataAttrs: Object.fromEntries(
                    Array.from(rows[i].attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                )
            });
        }
        return sampleRows;
    }""")

    # 5. Clickable trade links (Buy at / Sell at)
    results["trade_links"] = page.evaluate("""() => {
        const links = [];
        document.querySelectorAll('a, button').forEach(el => {
            const text = el.textContent?.trim().toLowerCase() || '';
            if (text.includes('buy') || text.includes('sell') || text.includes('trade') ||
                el.getAttribute('aria-label')?.toLowerCase().includes('buy') ||
                el.getAttribute('aria-label')?.toLowerCase().includes('sell') ||
                el.getAttribute('aria-label')?.toLowerCase().includes('trade')) {
                links.push({
                    tag: el.tagName,
                    text: el.textContent?.trim().substring(0, 100),
                    href: el.href || '',
                    class: el.className?.substring(0, 120),
                    ariaLabel: el.getAttribute('aria-label'),
                    dataAttrs: Object.fromEntries(
                        Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                    )
                });
            }
        });
        return links.slice(0, 50);
    }""")

    # 6. Greek/IV column detection
    results["greek_elements"] = page.evaluate("""() => {
        const items = [];
        const greekTerms = ['delta', 'gamma', 'theta', 'vega', 'rho', 'iv', 'implied', 'volatil'];
        document.querySelectorAll('th, td, [class*="greek"], [class*="Greek"], [data-column]').forEach(el => {
            const text = el.textContent?.trim().toLowerCase() || '';
            const cls = (el.className || '').toLowerCase();
            const matchesGreek = greekTerms.some(g => text.includes(g) || cls.includes(g));
            if (matchesGreek) {
                items.push({
                    tag: el.tagName,
                    text: el.textContent?.trim().substring(0, 60),
                    class: el.className?.substring(0, 120),
                    dataColumn: el.getAttribute('data-column'),
                    dataAttrs: Object.fromEntries(
                        Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                    )
                });
            }
        });
        return items.slice(0, 50);
    }""")

    # 7. Multi-leg / Strategy trade links
    results["strategy_links"] = page.evaluate("""() => {
        const items = [];
        document.querySelectorAll('a, button, [role="menuitem"], [role="tab"]').forEach(el => {
            const text = el.textContent?.trim().toLowerCase() || '';
            if (text.includes('strategy') || text.includes('multi') || text.includes('spread') ||
                text.includes('condor') || text.includes('straddle') || text.includes('strangle') ||
                text.includes('leg') || text.includes('custom')) {
                items.push({
                    tag: el.tagName,
                    text: el.textContent?.trim().substring(0, 100),
                    href: el.href || '',
                    class: el.className?.substring(0, 120),
                    ariaLabel: el.getAttribute('aria-label')
                });
            }
        });
        return items;
    }""")

    # 8. All iframes (Fidelity embeds content)
    results["iframes"] = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('iframe')).map(f => ({
            id: f.id,
            src: f.src,
            name: f.name,
            class: f.className?.substring(0, 120)
        }));
    }""")

    # 9. Forms (for trade tickets)
    results["forms"] = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('form')).map(f => ({
            id: f.id,
            action: f.action,
            method: f.method,
            name: f.name,
            class: f.className?.substring(0, 120),
            inputs: Array.from(f.querySelectorAll('input, select, textarea')).map(i => ({
                tag: i.tagName,
                name: i.name,
                id: i.id,
                type: i.type,
                placeholder: i.placeholder,
                ariaLabel: i.getAttribute('aria-label')
            }))
        }));
    }""")

    # 10. Shadow DOM roots (some modern Fidelity pages use web components)
    results["web_components"] = page.evaluate("""() => {
        const items = [];
        document.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) {
                items.push({
                    tag: el.tagName,
                    id: el.id,
                    class: el.className?.substring(0, 120)
                });
            }
        });
        return items.slice(0, 30);
    }""")

    # 11. Full page screenshot
    page.screenshot(path="options_chain_screenshot.png", full_page=True)
    results["screenshot_saved"] = "options_chain_screenshot.png"

    return results


def main():
    print("Launching browser to explore Fidelity options chain...")
    print(f"Target URL: {OPTIONS_URL}\n")

    pw = sync_playwright().start()

    # Check for saved cookies
    cookie_files = [f for f in os.listdir('.') if f.startswith('Fidelity_') and f.endswith('.json')]
    storage_state = cookie_files[0] if cookie_files else None

    if storage_state:
        print(f"Found saved cookies: {storage_state}")
    else:
        print("No saved cookies found - you'll need to log in manually")

    # Launch Firefox (matching FidelityAutomation's browser choice)
    browser = pw.firefox.launch(
        headless=False,
        args=["--disable-webgl", "--disable-software-rasterizer"],
    )

    context = browser.new_context(
        storage_state=storage_state
    )
    page = context.new_page()

    # Apply stealth if available
    if HAS_STEALTH:
        stealth = Stealth()
        stealth.apply_stealth_sync(page)
        print("Stealth mode applied")

    try:
        print("Navigating to options chain page...")
        page.goto(OPTIONS_URL, timeout=30000)

        # Wait for page to fully load
        print("Waiting for page to load...")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            print("Network idle timeout - page may still be loading dynamic content")

        # Extra wait for dynamic JS rendering
        page.wait_for_timeout(3000)

        print(f"Page title: {page.title()}")
        print(f"Page URL:   {page.url}\n")

        # Check if we need to log in
        if "login" in page.url.lower():
            print("=" * 60)
            print("  LOGIN REQUIRED")
            print("  Please log in to your Fidelity account in the browser window.")
            print("  The script will continue once you're on the options page.")
            print("=" * 60)
            # Wait for navigation away from login page
            page.wait_for_url("**/options-research/**", timeout=120000)
            page.wait_for_load_state("networkidle", timeout=20000)
            page.wait_for_timeout(3000)
            print("Login detected! Continuing...")

        # Now explore the page
        print("Extracting page structure...")
        results = explore_options_chain(page)

        # Print summary
        print(f"\n{'='*60}")
        print(f"  EXPLORATION RESULTS SUMMARY")
        print(f"{'='*60}")

        print(f"\n  Tables found: {len(results['tables'])}")
        for t in results['tables']:
            print(f"    Table #{t['index']}: {t['rowCount']} rows, {t['headerCount']} headers")
            if t['headers']:
                header_texts = [h['text'] for h in t['headers'] if h['text']]
                print(f"      Headers: {', '.join(header_texts[:15])}")

        print(f"\n  Expiration selectors: {len(results['expiration_selectors'])}")
        for e in results['expiration_selectors'][:5]:
            print(f"    <{e['tag'].lower()}> text=\"{e['text'][:60]}\" class=\"{e.get('class', '')[:60]}\"")

        print(f"\n  Trade links (buy/sell): {len(results['trade_links'])}")
        for l in results['trade_links'][:10]:
            print(f"    <{l['tag'].lower()}> text=\"{l['text'][:60]}\" href=\"{l.get('href', '')[:80]}\"")

        print(f"\n  Greek-related elements: {len(results['greek_elements'])}")
        for g in results['greek_elements'][:10]:
            print(f"    <{g['tag'].lower()}> text=\"{g['text'][:40]}\" data-column=\"{g.get('dataColumn', '')}\"")

        print(f"\n  Strategy/multi-leg links: {len(results['strategy_links'])}")
        for s in results['strategy_links'][:5]:
            print(f"    <{s['tag'].lower()}> text=\"{s['text'][:60]}\"")

        print(f"\n  Iframes: {len(results['iframes'])}")
        print(f"  Forms: {len(results['forms'])}")
        print(f"  Web components (shadow DOM): {len(results['web_components'])}")

        # Save full results
        with open(OUTPUT_FILE, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n  Full results saved to: {OUTPUT_FILE}")
        print(f"  Screenshot saved to: options_chain_screenshot.png")

        # Also save cookies for future use
        if not storage_state:
            context.storage_state(path="Fidelity_explore.json")
            print("  Cookies saved to: Fidelity_explore.json")

        input("\nPress Enter to close browser...")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close browser...")

    finally:
        context.close()
        browser.close()
        pw.stop()


if __name__ == "__main__":
    main()
