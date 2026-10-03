"""Read-only Chromium connectivity smoke test for the runtime worker image."""
from __future__ import annotations

from pathlib import Path
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright


def main() -> None:
    tracker = Path("/app/agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx")
    if not tracker.is_file() or tracker.stat().st_size == 0:
        raise RuntimeError("Runtime tracker is unavailable")
    deadline = time.monotonic() + 45
    while True:
        try:
            with urllib.request.urlopen("http://web:8000/health", timeout=3) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Runtime web did not become healthy within 45 seconds")
        time.sleep(2)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            response = page.goto("http://web:8000/health", timeout=15000)
            if response is None or response.status != 200:
                raise RuntimeError("Runtime web health was not reachable from Chromium")
        finally:
            browser.close()
    print("Runtime worker Chromium and web connectivity OK")


if __name__ == "__main__":
    main()
