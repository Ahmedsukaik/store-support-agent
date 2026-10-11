"""Record the demo story and the README images from a running demo.

    python demo.py                                   (in another terminal), or a deployed URL
    python docs/make_media.py http://127.0.0.1:8300

Needs Playwright with an installed Chrome or Edge, and ffmpeg on the PATH for the GIF.
Writes docs/img/tour.gif, docs/img/tour.mp4 and docs/img/screen-*.png.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "img"
SIZE = {"width": 1440, "height": 900}


def main(url: str) -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch(channel="chrome")
        context = browser.new_context(viewport=SIZE, device_scale_factor=1, record_video_dir=tmp, record_video_size=SIZE)
        page = context.new_page()
        click = lambda name: page.get_by_role("button", name=name, exact=True).click()  # noqa: E731
        page.goto(url + "/")
        page.get_by_role("button", name="Start the demo").wait_for()
        page.wait_for_timeout(2200)
        page.screenshot(path=str(OUT / "screen-intro.png"))
        click("Start the demo")
        page.wait_for_timeout(1500)
        click("Send Maya's message")
        page.locator(".story-title", has_text="Your turn").wait_for(timeout=90000)
        page.wait_for_timeout(2600)
        page.screenshot(path=str(OUT / "screen-approve.png"))
        click("Approve refund")
        page.locator(".story-title", has_text="Maya hears back").wait_for()
        page.wait_for_timeout(3200)
        page.screenshot(path=str(OUT / "screen-approved.png"))
        click("Next: try to trick it")
        page.wait_for_timeout(1200)
        click("Send the trick")
        page.get_by_role("button", name="Next: catch a bad reply").wait_for(timeout=90000)
        page.wait_for_timeout(3000)
        click("Next: catch a bad reply")
        page.wait_for_timeout(1200)
        click("Send the bad draft")
        page.get_by_role("button", name="See what it cost").wait_for(timeout=90000)
        page.wait_for_timeout(3200)
        page.screenshot(path=str(OUT / "screen-held.png"))
        click("See what it cost")
        page.wait_for_timeout(3500)
        page.screenshot(path=str(OUT / "screen-cost.png"))
        video = page.video.path() if page.video else None
        context.close()
        browser.close()
        if video:
            webm = Path(tmp) / "tour.webm"
            shutil.copy(video, webm)
            mp4 = OUT / "tour.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-crf", "23", "-movflags", "+faststart", str(mp4)], check=True)
            palette = Path(tmp) / "palette.png"
            scale = "fps=7,scale=960:-1:flags=lanczos"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf", f"{scale},palettegen=max_colors=128",
                            str(palette)], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-i", str(palette), "-lavfi",
                            f"{scale} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4", str(OUT / "tour.gif")], check=True)
    print("written:", ", ".join(sorted(f.name for f in OUT.iterdir())))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8300")
