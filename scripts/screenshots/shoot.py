"""Takes the README screenshots from a running gateway, through the real web UI.

  shoot.py BASE_URL OUT_DIR [--startup]

Default set: the answer to a question (light, dark, phone), search results, the services with a test
run, the API docs. --startup only captures the startup banner (models downloading): run it right after
`docker compose up -d` on a fresh install.
Only the example notes should be on screen: never point it at a gateway serving private notes.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE, OUT = sys.argv[1].rstrip("/"), sys.argv[2]
QUESTION = "How much did the bathroom repair cost, and how did I pay?"
SCAN = "/fixtures/invoice-scan.pdf"  # the invented bill, as a scan (documents profile only)
DIALOGUE = "/speech/dialogue.ogg"     # two public-domain synthetic voices (transcribe + speakers profiles)
DESKTOP = {"width": 1180, "height": 860}
PHONE = {"width": 390, "height": 844}


def page(browser, viewport, theme="light", lang="en", tab="ask", scale=2):
    ctx = browser.new_context(viewport=viewport, device_scale_factor=scale,
                              color_scheme="dark" if theme == "dark" else "light")
    p = ctx.new_page()
    p.goto(f"{BASE}/?tab={tab}&theme={theme}&lang={lang}")
    p.wait_for_function("document.querySelector('#st-ollama').textContent !== '—'")
    return p


def ask(p, question=QUESTION):
    p.fill("#ask-q", question)
    p.click("#ask-go")
    p.wait_for_function("document.querySelector('#ask-meta').textContent.length > 0", timeout=180_000)
    p.wait_for_timeout(3500)  # one status refresh, so the queue shows idle again


def shot(p, name, full=False):
    p.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    print("✓", name)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    if "--startup" in sys.argv:
        p = page(b, DESKTOP)
        p.wait_for_selector("#startup:not([hidden])", timeout=60_000)
        p.wait_for_timeout(1500)
        shot(p, "startup")
        sys.exit(0)

    # warm-up: the first answer also loads the model into VRAM, and its time would be misleading
    ask(page(b, DESKTOP), "warm up")

    for theme in ("light", "dark"):
        p = page(b, DESKTOP, theme)
        ask(p)
        shot(p, f"ask-{theme}", full=True)

    p = page(b, PHONE, "light", tab="ask", scale=3)
    ask(p, "When is the stove serviced?")
    shot(p, "phone", full=True)

    p = page(b, DESKTOP, "light", lang="it")
    ask(p, "Quanto ho speso per l'idraulico?")
    shot(p, "ask-italian", full=True)

    p = page(b, DESKTOP, tab="search")
    p.fill("#search-q", "the heating shows an error code")
    p.click("#search-form button[type=submit]")
    p.wait_for_selector("#search-out .hit")
    shot(p, "search", full=True)

    p = page(b, DESKTOP, tab="services")
    p.wait_for_selector(".card [data-test]")
    p.click(".card [data-test='search']")
    p.wait_for_function("document.querySelectorAll('.card .checks li').length >= 8", timeout=300_000)
    p.wait_for_timeout(3500)
    shot(p, "services", full=True)

    if os.path.exists(SCAN):
        p = page(b, DESKTOP, tab="documents")
        p.wait_for_function("!document.querySelector('#doc-go').disabled", timeout=30_000)
        p.set_input_files("#doc-file", SCAN)
        p.click("#doc-go")
        p.wait_for_selector("#doc-out:not([hidden])", timeout=600_000)
        p.wait_for_timeout(3500)
        shot(p, "documents", full=True)

        p = page(b, DESKTOP)
        ask(p, "How much is the electricity bill in total, and when will it be paid?")
        shot(p, "ask-library", full=True)

    if os.path.exists(DIALOGUE):
        p = page(b, DESKTOP, tab="transcribe")
        p.wait_for_function("!document.querySelector('#tr-go').disabled", timeout=30_000)
        p.set_input_files("#tr-file", DIALOGUE)
        if p.is_enabled("#tr-spk"):
            p.check("#tr-spk")
        p.click("#tr-go")
        p.wait_for_selector("#tr-out:not([hidden])", timeout=600_000)
        p.wait_for_timeout(3500)
        shot(p, "transcribe", full=True)

    p = b.new_context(viewport=DESKTOP, device_scale_factor=2).new_page()
    p.goto(f"{BASE}/docs")
    p.wait_for_selector(".opblock", timeout=30_000)
    shot(p, "api-docs")
    b.close()
