"""Render the app icon (same design as static/img/icon.svg) to PNG/ICO with Pillow.

    python packaging/make_icons.py
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))


def render(size: int) -> Image.Image:
    s = size / 64.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=14 * s, fill=(13, 17, 23, 255),
                        outline=(48, 54, 61, 255), width=max(1, round(3 * s)))
    cyan = (34, 211, 238, 255)
    w = max(2, round(6 * s))
    d.line([(32 * s, 14 * s), (32 * s, 38 * s)], fill=cyan, width=w)
    d.line([(22 * s, 28 * s), (32 * s, 38 * s), (42 * s, 28 * s)], fill=cyan, width=w, joint="curve")
    d.line([(18 * s, 48 * s), (46 * s, 48 * s)], fill=cyan, width=w)
    r = w / 2
    for (x, y) in [(32 * s, 14 * s), (22 * s, 28 * s), (42 * s, 28 * s), (18 * s, 48 * s), (46 * s, 48 * s), (32 * s, 38 * s)]:
        d.ellipse([x - r, y - r, x + r, y + r], fill=cyan)
    return img


if __name__ == "__main__":
    big = render(1024).resize((512, 512), Image.LANCZOS)
    big.save(os.path.join(HERE, "icon.png"))
    big.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote packaging/icon.png and packaging/icon.ico")
