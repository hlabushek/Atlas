"""No-network Chromium startup and JavaScript-rendered DOM check."""
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content('<div id="root"></div>')
    page.evaluate("document.querySelector('#root').textContent = 'JS_OK'")
    assert page.locator('#root').inner_text() == 'JS_OK'
    browser.close()
print('PASS: isolated Chromium launched and rendered JavaScript')
