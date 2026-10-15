"""Times the watch page in a real browser: turn it on, then flip channels up.

Runs in the fs42-playwright image (Playwright plus Google Chrome, which plays H.264 and AAC;
Playwright's own Chromium doesn't):

    docker run --rm --network host -v ~/fieldstation42-live/fs42_live/tools:/t -v ~/playwright-out:/out \
        fs42-playwright python3 /t/test_watch.py [flips] [url]

Prints how long each channel took to show a moving picture, any page errors, and saves a
screenshot of each channel to /out.
"""

import sys
import time

from playwright.sync_api import sync_playwright

FLIPS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
URL = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:4242/live/watch"
PLAYING = """() => {
    const v = document.getElementById("tv");
    return document.getElementById("snow").classList.contains("hidden") && !v.paused && v.currentTime > 0;
}"""


def tuned_in(page, label):
    start = time.monotonic()
    try:
        page.wait_for_function(PLAYING, timeout=30000)
        took = f"{time.monotonic() - start:.1f} s"
    except Exception:
        took = "no picture after 30 s"
    channel = page.evaluate("location.hash.slice(1)")
    print(f"{label} ch {channel}: {took}", flush=True)
    page.wait_for_timeout(1500)  # let the picture settle before the screenshot
    page.screenshot(path=f"/out/ch{channel}.png")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(URL + "#12")
    page.click("#start")
    tuned_in(page, "turn on,")
    for _ in range(FLIPS):
        page.keyboard.press("ArrowUp")
        tuned_in(page, "channel up,")
    browser.close()
    print("page errors:", errors or "none")
