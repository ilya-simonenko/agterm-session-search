#!/usr/bin/env python3
"""Render a session background: a dark wash in the emoji's own colour, the emoji large and faint on the
right, and the title in faint type below it. Needs Pillow; the emoji hook skips backgrounds without it.

usage: session-background.py <out.png> <emoji> [title]   (to preview one)
"""

import colorsys
import hashlib
import random
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
TEXT_FONT = "/System/Library/Fonts/SFNS.ttf"
WIDTH, HEIGHT = 1440, 900
STYLE = 2  # bump when the look changes, so cached images are rendered again


def emoji_image(emoji):
    # Apple Color Emoji only has bitmap strikes, 160 being the largest: draw at that size, trim, scale up
    font = ImageFont.truetype(EMOJI_FONT, 160)
    img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((200, 200), emoji, font=font, embedded_color=True, anchor="mm")
    box = img.getbbox()
    if not box:
        raise ValueError(f"no glyph for {emoji!r}")
    return img.crop(box)


def dominant_hue(img):
    """Median hue of the emoji's saturated opaque pixels; a grey emoji gives a neutral blue."""
    raw = img.resize((48, 48)).tobytes()  # RGBA bytes; getdata() is deprecated in recent Pillow
    hsvs = [colorsys.rgb_to_hsv(raw[i] / 255, raw[i + 1] / 255, raw[i + 2] / 255)
            for i in range(0, len(raw), 4) if raw[i + 3] > 128]
    hues = sorted(h for h, s, v in hsvs if s > 0.25 and v > 0.2)
    return hues[len(hues) // 2] if hues else 0.6


def solid(hue, sat, val, size=(WIDTH, HEIGHT)):
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, sat, val)
    return Image.new("RGB", size, (int(r * 255), int(g * 255), int(b * 255)))


def render(emoji, title=""):
    rng = random.Random(hashlib.sha256((emoji + title).encode()).digest())
    glyph = emoji_image(emoji)
    hue = dominant_hue(glyph)

    # a dark wash, lighter at the top, with a few soft glows in the same hue family
    fade = Image.linear_gradient("L").resize((WIDTH, HEIGHT))
    img = Image.composite(solid(hue, 0.55, 0.05), solid(hue, 0.55, 0.15), fade)
    for _ in range(3):
        rx, ry = int(rng.uniform(0.25, 0.5) * WIDTH), int(rng.uniform(0.3, 0.6) * HEIGHT)
        cx, cy = int(rng.uniform(0.3, 1.0) * WIDTH), int(rng.uniform(0.1, 0.9) * HEIGHT)
        mask = Image.new("L", (WIDTH, HEIGHT), 0)
        glow = Image.radial_gradient("L").point(lambda v: int(max(0, 255 - v * 1.4) * 0.7)).resize((2 * rx, 2 * ry))
        mask.paste(glow, (cx - rx, cy - ry))
        img = Image.composite(solid(hue + rng.uniform(-0.06, 0.06), 0.7, 0.3), img, mask)

    # the emoji, clearly visible but a little see-through so terminal text on top stays readable
    scale = HEIGHT * 0.66 / max(glyph.size)  # the longer side, so a tall emoji fits too
    glyph = glyph.resize((int(glyph.width * scale), int(glyph.height * scale)), Image.LANCZOS)
    glyph = glyph.filter(ImageFilter.GaussianBlur(1.0))
    r, g, b, a = glyph.split()
    dim = lambda v: int(v * 0.95)  # noqa: E731
    glyph = Image.merge("RGBA", (r.point(dim), g.point(dim), b.point(dim), a.point(lambda v: int(v * 0.82))))
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    layer.paste(glyph, (int(WIDTH * 0.94) - glyph.width, int(HEIGHT * 0.44) - glyph.height // 2), glyph)
    if title:
        font = ImageFont.truetype(TEXT_FONT, int(HEIGHT * 0.05))
        while font.getlength(title) > WIDTH * 0.8 and len(title) > 4:
            title = title[:-2].rstrip() + "…"
        ImageDraw.Draw(layer).text((int(WIDTH * 0.94), int(HEIGHT * 0.93)), title, font=font,
                                   fill=(255, 255, 255, 100), anchor="rs")
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def save(path, emoji, title=""):
    render(emoji, title).save(path, optimize=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip())
    save(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
