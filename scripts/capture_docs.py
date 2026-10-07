"""Capture README screenshots and a demo GIF from the running Streamlit app (needs playwright + ffmpeg).

Usage: python scripts/capture_docs.py
Seeds demo data, makes 3 real suite runs, starts the app headless on port 8599, then writes
docs/suite-builder.png, docs/results.png, docs/history.png, docs/report.png and docs/demo.gif.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PORT = 8599
URL = f"http://localhost:{PORT}"
SIZE = {"width": 1440, "height": 900}


def sh(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def goto_page(page: Page, name: str) -> None:
    page.locator('[data-testid="stSidebar"] label').filter(has_text=name).first.click()
    page.wait_for_timeout(1800)


def pick(page: Page, index: int, option: str) -> None:
    page.locator('[data-testid="stSelectbox"]').nth(index).click()
    page.get_by_role("option", name=option, exact=True).click()
    page.wait_for_timeout(1200)


def scroll_main(page: Page, px: int) -> None:
    page.locator('[data-testid="stMain"]').evaluate(f"e => e.scrollTop = {px}")
    page.wait_for_timeout(600)


def main() -> None:
    py = sys.executable
    shutil.rmtree(ROOT / "reports", ignore_errors=True)
    DOCS.mkdir(exist_ok=True)
    sh(py, "scripts/seed_demo.py")
    for _ in range(3):
        sh(py, "-m", "recon", "run", "suites/demo_migration.yaml")
        time.sleep(1.1)  # distinct timestamps
    server = subprocess.Popen([py, "-m", "streamlit", "run", "apps/streamlit_app.py", "--server.headless", "true",
                               "--server.port", str(PORT), "--client.toolbarMode", "viewer", "--browser.gatherUsageStats", "false"],
                              cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(6)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport=SIZE)
            page.goto(URL)
            page.wait_for_selector('[data-testid="stSidebar"]')
            page.wait_for_timeout(2000)

            goto_page(page, "Suite Builder")
            pick(page, 1, "orders")
            page.get_by_role("button", name="Preview source & target").click()
            page.wait_for_timeout(2500)
            scroll_main(page, 760)
            page.screenshot(path=DOCS / "suite-builder.png")

            goto_page(page, "Results")
            scroll_main(page, 640)
            page.screenshot(path=DOCS / "results.png")

            goto_page(page, "History")
            scroll_main(page, 60)
            page.screenshot(path=DOCS / "history.png")

            report = sorted((ROOT / "reports").glob("*.html"))[-1]
            rp = browser.new_page(viewport=SIZE)
            rp.goto(report.as_uri())
            rp.screenshot(path=DOCS / "report.png")

            vid_dir = ROOT / "reports" / "video"
            ctx = browser.new_context(viewport=SIZE, record_video_dir=str(vid_dir), record_video_size=SIZE)
            v = ctx.new_page()
            v.goto(URL)
            v.wait_for_selector('[data-testid="stSidebar"]')
            v.wait_for_timeout(2500)
            goto_page(v, "Run")
            v.wait_for_timeout(2000)
            v.get_by_role("button", name="Run reconciliation").click()
            v.wait_for_selector("text=Suite status", timeout=60000)
            v.wait_for_timeout(3500)
            goto_page(v, "Results")
            v.wait_for_timeout(3000)
            scroll_main(v, 640)
            v.wait_for_timeout(4500)
            scroll_main(v, 1100)
            v.wait_for_timeout(2500)
            ctx.close()
            browser.close()
        webm = sorted(vid_dir.glob("*.webm"))[-1]
        sh("ffmpeg", "-y", "-ss", "1.5", "-t", "22", "-i", str(webm), "-vf",
           "fps=8,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
           str(DOCS / "demo.gif"))
    finally:
        server.terminate()
    print({f.name: f"{f.stat().st_size / 1e6:.2f} MB" for f in sorted(DOCS.iterdir())})


if __name__ == "__main__":
    main()
