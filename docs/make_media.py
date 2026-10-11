"""Record the demo tour and the README images from a running demo.

    python demo.py                                   (in another terminal)
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
        page.goto(url + "/")
        page.locator(".starter").first.wait_for()
        page.wait_for_timeout(1500)
        page.locator("#play-tour").click()
        page.locator(".notice.approved").wait_for(timeout=60000)
        page.wait_for_timeout(3500)
        page.screenshot(path=str(OUT / "screen-approved.png"))
        page.get_by_role("tab", name="Cost").click()
        page.wait_for_timeout(1800)
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
