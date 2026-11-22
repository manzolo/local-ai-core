"""Builds the test documents of the documents service from an invented HTML page.

  make_fixtures.py INPUT.html OUT_DIR

  OUT_DIR/invoice.pdf        printed by Chromium: real text, real table
  OUT_DIR/invoice-scan.pdf   the same page as a picture, turned by 1.2°, grey, noisy, slightly blurred:
                             the only way to read it is OCR
Deterministic: the noise has a fixed seed, so the files change only when the HTML does.
"""
import io
import random
import sys

from PIL import Image, ImageFilter
from playwright.sync_api import sync_playwright

src, out = sys.argv[1], sys.argv[2].rstrip("/")
html = open(src, encoding="utf-8").read()

with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=2)  # A4 at 96 dpi
    p.set_content(html)
    p.pdf(path=f"{out}/invoice.pdf", format="A4", print_background=True)
    png = p.screenshot(full_page=False)
    b.close()

img = Image.open(io.BytesIO(png)).convert("L")
img = img.rotate(1.2, resample=Image.BICUBIC, expand=True, fillcolor=255)
rnd = random.Random(2093)
px = img.load()
w, h = img.size
for _ in range(w * h // 60):  # specks of dust
    x, y = rnd.randrange(w), rnd.randrange(h)
    px[x, y] = rnd.choice((90, 140, 190))
img = img.filter(ImageFilter.GaussianBlur(0.6))
img.save(f"{out}/invoice-scan.pdf", "PDF", resolution=192)
print(f"✓ {out}/invoice.pdf, {out}/invoice-scan.pdf")
