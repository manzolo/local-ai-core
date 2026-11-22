"""Builds the test page of the handwriting service: an invented note in a handwriting font.

  make_handwriting.py OUT_DIR

The font is Caveat (SIL Open Font License), downloaded from google/fonts at a pinned commit and used only
to draw the picture: the font file is not kept. Lined paper, blue ink, a slight turn and some noise, like a
phone photo of a notebook page. Deterministic.
"""
import io
import random
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = ("https://raw.githubusercontent.com/google/fonts/06bfe0d62a98375cb09c942fa18e68bd32cff057/"
        "ofl/caveat/Caveat%5Bwght%5D.ttf")
LINES = [
    "Saturday, 14 March",
    "Buy flour, eggs and milk.",
    "Call the plumber on Monday at nine.",
    "Water the tomatoes in the evening.",
    "Return the library books by Friday.",
]


def main():
    out = sys.argv[1].rstrip("/")
    font = ImageFont.truetype(io.BytesIO(urllib.request.urlopen(FONT, timeout=60).read()), 64)
    w, h, gap, top = 1400, 900, 120, 150
    page = Image.new("RGB", (w, h), (250, 248, 240))
    d = ImageDraw.Draw(page)
    for y in range(top + 20, h, gap):
        d.line((0, y, w, y), fill=(170, 200, 230), width=2)
    d.line((110, 0, 110, h), fill=(230, 150, 150), width=2)
    rnd = random.Random(14)
    for i, text in enumerate(LINES):
        d.text((140 + rnd.randint(-6, 6), top - 60 + i * gap + rnd.randint(-4, 4)), text, font=font,
               fill=(25, 45, 120))
    page = page.rotate(-1.5, resample=Image.BICUBIC, expand=False, fillcolor=(235, 233, 225))
    px = page.load()
    for _ in range(w * h // 80):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r, g, b = px[x, y]
        k = rnd.randint(-18, 18)
        px[x, y] = (max(0, min(255, r + k)), max(0, min(255, g + k)), max(0, min(255, b + k)))
    page = page.filter(ImageFilter.GaussianBlur(0.5))
    page.save(f"{out}/note.jpg", quality=88)
    with open(f"{out}/note.txt", "w") as f:
        f.write("\n".join(LINES) + "\n")
    print(f"✓ {out}/note.jpg")


if __name__ == "__main__":
    main()
