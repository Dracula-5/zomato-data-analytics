"""Screenshot every dashboard page for the README, and check each one in light and dark mode.

Needs the dashboard running and Chrome installed. Playwright is installed only for this run:

    make app                    # in one terminal
    make screenshots            # in another

Plain headless Chrome only captures Streamlit's loading skeleton, because the page fills in over
a websocket. Playwright waits for the real content.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
DARK = ROOT / "artifacts" / "screenshots_dark"  # to look at, not to publish
PAGES = {
    "home": "",
    "cities": "Cities",
    "restaurants": "Restaurants",
    "delivery_sla": "Delivery_SLA",
    "data_quality": "Data_Quality",
    "forecasts": "Forecasts",
    "knowledge_graph": "Knowledge_Graph",
    "ai_evaluation": "AI_Evaluation",
}
QUESTION = "Which restaurant chains did the graph find?"
MAX_HEIGHT = 2600
IDLE = "!document.querySelector('[data-testid=\"stStatusWidget\"]')"


def errors_on(page: Page) -> int:
    return page.locator('[data-testid="stException"]').count()


def settle(page: Page) -> None:
    """Wait for Streamlit to finish, however long a first query takes, then for the charts."""
    page.wait_for_timeout(1500)
    page.wait_for_function(IDLE, timeout=180000)
    page.wait_for_timeout(2500)


def capture(page: Page, target: Path) -> int:
    page.locator('[data-testid="stMainBlockContainer"]').wait_for(timeout=60000)
    settle(page)
    height = page.evaluate(
        "document.querySelector('[data-testid=\"stMainBlockContainer\"]').scrollHeight"
    )
    # Streamlit scrolls an inner container, so the window has to grow to fit everything.
    page.set_viewport_size({"width": 1400, "height": min(height + 80, MAX_HEIGHT)})
    settle(page)
    page.screenshot(path=str(target))
    return errors_on(page)


def main(base: str) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    DARK.mkdir(parents=True, exist_ok=True)
    problems = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        for scheme in ("light", "dark"):
            for name, path in PAGES.items():
                page = browser.new_page(
                    viewport={"width": 1400, "height": 1000}, color_scheme=scheme
                )
                page.goto(f"{base}/{path}")
                target = (OUT if scheme == "light" else DARK) / f"{name}.png"
                found = capture(page, target)
                problems += found
                print(f"{scheme:5} {name:16} errors: {found}")
                page.close()

        page = browser.new_page(viewport={"width": 1400, "height": 1200}, color_scheme="light")
        page.goto(f"{base}/Copilot")
        example = page.get_by_role("button", name=QUESTION)
        example.wait_for(timeout=60000)
        example.click()
        page.get_by_text("numbers trace to tool output").wait_for(timeout=60000)
        page.wait_for_timeout(1500)
        problems += errors_on(page)
        page.screenshot(path=str(OUT / "copilot.png"))
        print(f"light copilot          errors: {errors_on(page)}")
        browser.close()
    return problems


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501") else 0)
