#!/usr/bin/env python3
"""Build the AshuLab brand assets.

Every SVG in this folder is generated here, and every PNG is rasterised from
that SVG. Nothing is upscaled. The mark is centred by measurement rather than
by hand: the ink's bounding box is rendered, compared with the badge centre and
the group is translated until the two agree, so the geometry cannot drift.

Requires cairosvg and Pillow:

    python3 -m venv .venv && .venv/bin/pip install cairosvg pillow
    .venv/bin/python build.py
"""
from __future__ import annotations

import io
import pathlib

import cairosvg
from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).parent
PNG = HERE / "png"

NAVY = "#0B1220"      # badge base, and the ink on light backgrounds
CYAN = "#2DD4FF"      # accent, the mark on the badge
DEEP = "#0E7490"      # accent for light backgrounds: same hue, legible on white
PAPER = "#E8EEF5"     # wordmark on dark backgrounds

# Geometry in badge units: a 512 canvas with the badge centred on 256,256.
APEX = (256, 146)
LEFT = (146, 372)
RIGHT = (366, 372)
DOT = 26              # node radius, identical on all three nodes
STROKE = 28
CROSS_Y = 290
SCALE = 1.10

FONT = "Ubuntu Sans"
FONT_WEIGHTS = {"light": 400, "bold": 700}
BADGE_SIZES = (512, 256, 128, 64, 32)
LOCKUP_WIDTHS = (1600, 800)


def crossbar_x(y: float) -> tuple[float, float]:
    t = (y - APEX[1]) / (LEFT[1] - APEX[1])
    return APEX[0] + (LEFT[0] - APEX[0]) * t, APEX[0] + (RIGHT[0] - APEX[0]) * t


def mark_group(dx: float = 0.0, dy: float = 0.0, ink: str = CYAN) -> str:
    """The cluster-A: three nodes on two legs, with a crossbar."""
    x1, x2 = crossbar_x(CROSS_Y)
    t = "translate(%.2f,%.2f) translate(256,256) scale(%.3f) translate(-256,-256)" % (dx, dy, SCALE)
    return "\n  ".join([
        f'<g transform="{t}" stroke="{ink}" stroke-width="{STROKE}" '
        'stroke-linecap="round" stroke-linejoin="round" fill="none">',
        f'  <line x1="{APEX[0]}" y1="{APEX[1]}" x2="{LEFT[0]}" y2="{LEFT[1]}"/>',
        f'  <line x1="{APEX[0]}" y1="{APEX[1]}" x2="{RIGHT[0]}" y2="{RIGHT[1]}"/>',
        f'  <line x1="{x1:.1f}" y1="{CROSS_Y}" x2="{x2:.1f}" y2="{CROSS_Y}"/>',
        '</g>',
        f'<g transform="{t}" fill="{ink}">',
        f'  <circle cx="{APEX[0]}" cy="{APEX[1]}" r="{DOT}"/>',
        f'  <circle cx="{LEFT[0]}" cy="{LEFT[1]}" r="{DOT}"/>',
        f'  <circle cx="{RIGHT[0]}" cy="{RIGHT[1]}" r="{DOT}"/>',
        '</g>',
    ])


def svg(body: str, w: int = 512, h: int = 512) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}">\n  {body}\n</svg>\n')


def ink_box(source: str, size: int, bg_hex: str | None) -> tuple[float, float, float, float]:
    """Bounding box of non-background pixels, in source units."""
    png = cairosvg.svg2png(bytestring=source.encode(), output_width=size, output_height=size)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    if bg_hex:
        r, g, b = (int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
        ref = (r, g, b)
        diff = lambda p: sum(abs(p[i] - ref[i]) for i in range(3)) > 30
    else:
        diff = lambda p: p[3] > 40
    xs = [x for y in range(size) for x in range(size) if diff(img.getpixel((x, y)))]
    ys = [y for y in range(size) for x in range(size) if diff(img.getpixel((x, y)))]
    k = 512 / size
    return min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k


def centred_mark(ink: str) -> str:
    """Shift the mark until its ink is centred on the 256,256 badge centre."""
    dx = dy = 0.0
    for _ in range(4):
        box = ink_box(svg(mark_group(dx, dy, ink)), 512, None)
        ox = (box[0] + box[2]) / 2 - 256
        oy = (box[1] + box[3]) / 2 - 256
        if abs(ox) < 0.4 and abs(oy) < 0.4:
            break
        dx, dy = dx - ox, dy - oy
    return mark_group(dx, dy, ink)


def text_width(text: str, size: int, weight: int) -> float:
    path = pathlib.Path(__import__("subprocess").run(
        ["fc-match", "-f", "%{file}", f"{FONT}:weight={weight}"],
        capture_output=True, text=True).stdout)
    return ImageFont.truetype(str(path), size).getlength(text)


def lockup(dark: bool) -> str:
    """Badge plus wordmark, on one line.

    The canvas is sized from the rendered ink, not from font metrics: Pango
    shapes wider than Pillow measures, which clips the last letter.
    """
    word, accent = (PAPER, CYAN) if dark else (NAVY, DEEP)
    size, badge, gap, pad = 158, 360, 64, 40
    text_x = pad + badge + gap
    height = badge + pad * 2
    centre_y = height / 2
    baseline = pad + badge * 0.70

    def wordmark(dy: float) -> str:
        return (f'<g transform="translate(0,{dy:.2f})"><text x="{text_x:.1f}" y="{baseline:.1f}" '
                f'font-family="{FONT}, DejaVu Sans, sans-serif" font-size="{size}" '
                f'font-weight="{FONT_WEIGHTS["light"]}" fill="{word}">Ashu'
                f'<tspan font-weight="{FONT_WEIGHTS["bold"]}" fill="{accent}">Lab</tspan></text></g>')

    def badge_group() -> str:
        return "\n  ".join([
            f'<g transform="translate({pad},{pad})">',
            f'  <circle cx="180" cy="180" r="180" fill="{NAVY}"/>',
            f'  <g transform="scale({badge / 512:.5f})">',
            centred_mark(CYAN).replace("\n  ", "\n    "),
            '  </g>',
            '</g>',
        ])

    # Vertical: align the wordmark's ink centre with the badge centre.
    probe = svg(wordmark(0), 2000, height)
    png = cairosvg.svg2png(bytestring=probe.encode(), output_width=2000, output_height=height)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    rows = [y for y in range(height) if any(img.getpixel((x, y))[3] > 40 for x in range(0, 2000, 4))]
    dy = centre_y - (min(rows) + max(rows)) / 2

    body = badge_group() + "\n  " + wordmark(dy)
    # Horizontal: the right edge of the ink plus the same padding.
    wide = svg(body, 3000, height)
    png = cairosvg.svg2png(bytestring=wide.encode(), output_width=3000, output_height=height)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    cols = [x for x in range(3000) if any(img.getpixel((x, y))[3] > 40 for y in range(0, height, 4))]
    width = int(max(cols) + pad + 1)
    return svg(body, width, height)


def main() -> None:
    PNG.mkdir(parents=True, exist_ok=True)
    centre = centred_mark(CYAN)

    sources = {
        "ashulab-badge": svg('<circle cx="256" cy="256" r="256" fill="%s"/>\n  %s' % (NAVY, centre)),
        "ashulab-badge-transparent": svg(centre),
        "ashulab-badge-mono-navy": svg(centred_mark(NAVY)),
        "ashulab-lockup-dark": lockup(dark=True),
        "ashulab-lockup-light": lockup(dark=False),
    }
    for name, source in sources.items():
        (HERE / f"{name}.svg").write_text(source)
        widths = LOCKUP_WIDTHS if name.startswith("ashulab-lockup") else BADGE_SIZES
        for w in widths:
            height = w if name.startswith("ashulab-badge") else None
            cairosvg.svg2png(bytestring=source.encode(), write_to=str(PNG / f"{name}-{w}.png"),
                             output_width=w, output_height=height)
        print(f"{name}.svg + {len(widths)} PNG")

    box = ink_box(sources["ashulab-badge"], 512, NAVY)
    print("badge ink centre: (%.2f, %.2f), expected (256, 256)"
          % ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))


if __name__ == "__main__":
    main()
