"""Real headless Chromium smoke test for the installed Playwright runtime."""

from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=30_000)
        try:
            page = browser.new_page()
            page.set_content("<!doctype html><title>JARVIS browser smoke</title><main>ready</main>")
            if page.title() != "JARVIS browser smoke":
                raise RuntimeError("Chromium did not render the smoke-test page title.")
            if page.locator("main").inner_text() != "ready":
                raise RuntimeError("Chromium did not render the smoke-test page body.")
            print("Headless Chromium launch and page rendering OK.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
