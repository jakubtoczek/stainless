"""Generate the Stainless app icon: a section (flat hematoxylin blue) with a brown DAB patch
in it, the patch ringed by a drawn polygon with its nodes, on the suite's dark rounded tile.
Flat shapes, no texture: it stays legible at 16 px.
Run: python misc/make_icon.py"""

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src" / "stainless" / "assets"
TILE, BLUE, BROWN, AMBER = (35, 35, 35, 255), (92, 124, 214, 255), (170, 104, 46, 255), (255, 176, 0, 255)


def render(size: int) -> Image.Image:
    ss = size * 8
    im = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, ss - 1, ss - 1], radius=ss // 5, fill=TILE)
    c, r = ss * 0.47, ss * 0.35                                   # the section
    section = Image.new("L", (ss, ss), 0)
    ImageDraw.Draw(section).ellipse([c - r, c - r, c + r, c + r], fill=255)
    im.paste(BLUE, (0, 0), section)
    patch = Image.new("L", (ss, ss), 0)                           # the stain, inside it
    pc, pr = ss * 0.60, ss * 0.20
    ImageDraw.Draw(patch).ellipse([pc - pr, pc - pr, pc + pr, pc + pr], fill=255)
    im.paste(BROWN, (0, 0), ImageChops.multiply(patch, section))
    k = [(math.radians(a), f) for a, f in ((-80, 1.0), (-10, 1.05), (55, 1.0), (125, 0.98),
                                           (195, 1.04))]
    gc, gr = (ss * 0.60, ss * 0.60), ss * 0.25                    # the drawn region
    pts = [(gc[0] + gr * f * math.cos(a), gc[1] + gr * f * math.sin(a)) for a, f in k]
    lw = max(2, round(ss * 0.03))
    d.line(pts + pts[:1], fill=AMBER, width=lw, joint="curve")
    n = ss * 0.036
    for x, y in pts:                                              # its nodes
        d.rectangle([x - n, y - n, x + n, y + n], fill=(255, 255, 255, 255), outline=AMBER,
                    width=max(1, lw // 2))
    return im.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [render(s) for s in sizes]
    imgs[-1].save(OUT / "stainless.png")
    imgs[-1].save(OUT / "stainless.ico", sizes=[(s, s) for s in sizes],
                  append_images=imgs[:-1])
    print("wrote", OUT / "stainless.ico", "and stainless.png")


if __name__ == "__main__":
    main()
