"""Generate the Stainless app icon: a section counterstained blue, a brown DAB-stained patch
in it, the patch circled by a drawn region, on the suite's dark rounded tile.
Run: python misc/make_icon.py"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src" / "stainless" / "assets"


def blob(cx, cy, rx, ry, wobble, n=90, phase=0.0):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = 1 + wobble * math.sin(3 * a + phase) + wobble * 0.6 * math.sin(5 * a + 2 * phase)
        pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
    return pts


def render(size: int) -> Image.Image:
    ss = size * 4
    im = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, ss - 1, ss - 1], radius=ss // 6, fill=(35, 35, 35, 255))
    d.polygon(blob(ss * 0.5, ss * 0.52, ss * 0.36, ss * 0.32, 0.07), fill=(150, 162, 196, 255))
    for i in range(14):                                   # hematoxylin nuclei
        a = i * 2.4
        r = ss * (0.08 + 0.22 * ((i * 7) % 10) / 10)
        x, y = ss * 0.5 + r * math.cos(a), ss * 0.52 + r * 0.85 * math.sin(a)
        k = ss * 0.022
        d.ellipse([x - k, y - k, x + k, y + k], fill=(70, 82, 150, 255))
    d.polygon(blob(ss * 0.62, ss * 0.62, ss * 0.15, ss * 0.12, 0.18, phase=1.0),
              fill=(139, 90, 43, 255))                    # the DAB patch
    lw = max(4, ss // 40)
    d.line(blob(ss * 0.62, ss * 0.62, ss * 0.22, ss * 0.19, 0.08, phase=2.0) +
           [blob(ss * 0.62, ss * 0.62, ss * 0.22, ss * 0.19, 0.08, phase=2.0)[0]],
           fill=(255, 176, 0, 255), width=lw, joint="curve")   # the drawn region
    return im.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [render(s) for s in sizes]
    imgs[-1].save(OUT / "stainless.png")
    imgs[-1].save(OUT / "stainless.ico", sizes=[(s, s) for s in sizes])
    print("wrote", OUT / "stainless.ico", "and stainless.png")


if __name__ == "__main__":
    main()
