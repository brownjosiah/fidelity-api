"""
Quick exploration script to dump DOM structure from Fidelity pages.
Run this AFTER logging in via the fidelity-api login flow.

Usage:
    python explore_page.py <url> [--selectors]

Examples:
    python explore_page.py "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry"
    python explore_page.py "https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=SPX" --selectors
"""

import sys
import json
from fidelity.fidelity import FidelityAutomation


def dump_page_structure(page, max_depth=3):
    """Extract key interactive elements from the page."""
    elements = page.evaluate("""(maxDepth) => {
        const results = {
            forms: [],
            buttons: [],
            inputs: [],
            selects: [],
            links: [],
            tables: [],
            iframes: [],
            dataElements: []
        };

        // Forms
        document.querySelectorAll('form').forEach(f => {
            results.forms.push({
                id: f.id,
                action: f.action,
                method: f.method,
                name: f.name
            });
        });

        // Buttons
        document.querySelectorAll('button, [role="button"], input[type="submit"]').forEach(b => {
            results.buttons.push({
                id: b.id,
                text: b.textContent?.trim().substring(0, 80),
                class: b.className?.substring(0, 100),
                type: b.type,
                ariaLabel: b.getAttribute('aria-label')
            });
        });

        // Inputs
        document.querySelectorAll('input, textarea').forEach(i => {
            results.inputs.push({
                id: i.id,
                name: i.name,
                type: i.type,
                placeholder: i.placeholder,
                ariaLabel: i.getAttribute('aria-label'),
                class: i.className?.substring(0, 100)
            });
        });

        // Selects / Dropdowns
        document.querySelectorAll('select, [role="listbox"], [role="combobox"]').forEach(s => {
            results.selects.push({
                id: s.id,
                name: s.name,
                ariaLabel: s.getAttribute('aria-label'),
                class: s.className?.substring(0, 100),
                optionCount: s.options?.length || 0
            });
        });

        // Tables (options chain data)
        document.querySelectorAll('table').forEach(t => {
            const headers = [];
            t.querySelectorAll('th').forEach(th => headers.push(th.textContent?.trim()));
            results.tables.push({
                id: t.id,
                class: t.className?.substring(0, 100),
                headers: headers.slice(0, 20),
                rowCount: t.querySelectorAll('tr').length
            });
        });

        // Iframes (Fidelity often embeds content)
        document.querySelectorAll('iframe').forEach(f => {
            results.iframes.push({
                id: f.id,
                src: f.src,
                name: f.name
            });
        });

        // Data elements (divs with price/greek data)
        document.querySelectorAll('[data-testid], [data-qa], [data-column]').forEach(d => {
            results.dataElements.push({
                tag: d.tagName,
                dataTestId: d.getAttribute('data-testid'),
                dataQa: d.getAttribute('data-qa'),
                dataColumn: d.getAttribute('data-column'),
                text: d.textContent?.trim().substring(0, 50),
                class: d.className?.substring(0, 80)
            });
        });

        return results;
    }""", max_depth)

    return elements


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None
    show_selectors = "--selectors" in sys.argv

    if not url:
        print("Usage: python explore_page.py <url> [--selectors]")
        print("\nCommon Fidelity URLs to explore:")
        print("  Options research: https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=SPX")
        print("  Equity trade:     https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry")
        print("  Positions:        https://digital.fidelity.com/ftgw/digital/portfolio/positions")
        return

    print(f"Starting browser and navigating to: {url}")
    print("(You'll need to log in if not using saved cookies)\n")

    # Use non-headless so you can see what's happening and log in if needed
    fid = FidelityAutomation(headless=False, title="explore", save_state=True)

    try:
        fid.page.goto(url, timeout=30000)
        fid.page.wait_for_load_state("networkidle", timeout=15000)

        print(f"Page title: {fid.page.title()}")
        print(f"Page URL:   {fid.page.url}\n")

        # Dump structure
        structure = dump_page_structure(fid.page)

        # Print summary
        for key, items in structure.items():
            if items:
                print(f"\n{'='*60}")
                print(f"  {key.upper()} ({len(items)} found)")
                print(f"{'='*60}")
                for item in items[:15]:  # Limit output
                    print(f"  {json.dumps(item, indent=4)}")

        if show_selectors:
            # Also dump all elements with IDs
            ids = fid.page.evaluate("""() => {
                return Array.from(document.querySelectorAll('[id]')).map(el => ({
                    tag: el.tagName,
                    id: el.id,
                    class: el.className?.substring(0, 60)
                })).slice(0, 100);
            }""")
            print(f"\n{'='*60}")
            print(f"  ALL ELEMENTS WITH IDS ({len(ids)} found)")
            print(f"{'='*60}")
            for el in ids:
                print(f"  <{el['tag'].lower()} id=\"{el['id']}\" class=\"{el['class']}\">")

        # Save full dump to file
        output_file = "page_structure.json"
        with open(output_file, "w") as f:
            json.dump(structure, f, indent=2)
        print(f"\nFull structure saved to: {output_file}")

        input("\nPress Enter to close browser...")

    finally:
        fid.close_browser()


if __name__ == "__main__":
    main()
