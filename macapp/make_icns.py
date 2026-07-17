# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Build an .icns app icon from the Sotto logo PNG.

The Dock tile is a macOS-style rounded square: the logo's warm-white paper
color fills a rounded rect, and the waveform mark (cropped from the left of
the wordmark — same background, so the crop blends seamlessly) sits centered.
Rendered once at 1024px, then scaled to every iconset size with sips and
packed with iconutil. Uses AppKit via pyobjc — no Pillow dependency.

Usage: python macapp/make_icns.py logo/sottoLogo.png build/Sotto.icns [--dev]
--dev stamps a coral DEV pill on the tile so the development build is
distinguishable from the released app at a glance (they are separate apps —
different bundle ids — and both may sit in the Dock).
"""
import os
import subprocess
import sys

CANVAS = 1024
TILE_INSET = 92        # transparent margin around the rounded tile (macOS grid)
CORNER = 185           # rounded-rect radius at 1024px
# x, y, w, h of the waveform mark as FRACTIONS of the logo (the PNG carries
# 2x-DPI metadata, so absolute pixel coords would be wrong in AppKit points)
MARK_CROP = (0.02, 0.05, 0.28, 0.90)
MARK_SPAN = 0.60       # mark height as a fraction of the tile
PAPER = (0.970, 0.968, 0.960)  # the logo's warm off-white background
SIZES = [16, 32, 128, 256, 512]


ACCENT = (0.851, 0.435, 0.341)  # dashboard coral #d96f57


def _draw_dev_badge():
    """Coral DEV pill, bottom-right of the tile. Caller must have a current
    graphics context."""
    from AppKit import (
        NSBezierPath, NSColor, NSFont, NSFontAttributeName,
        NSForegroundColorAttributeName, NSMakeRect, NSMakePoint, NSString)

    bw, bh = 316, 148
    bx = CANVAS - TILE_INSET - bw - 52
    by = TILE_INSET + 52
    NSColor.colorWithCalibratedRed_green_blue_alpha_(*ACCENT, 1.0).set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(bx, by, bw, bh), bh / 2, bh / 2).fill()
    attrs = {
        NSFontAttributeName: NSFont.boldSystemFontOfSize_(92),
        NSForegroundColorAttributeName: NSColor.whiteColor(),
    }
    text = NSString.stringWithString_("DEV")
    size = text.sizeWithAttributes_(attrs)
    text.drawAtPoint_withAttributes_(
        NSMakePoint(bx + (bw - size.width) / 2,
                    by + (bh - size.height) / 2), attrs)


def make_square_master(src: str, dst: str, dev: bool = False):
    from AppKit import (
        NSBezierPath,
        NSBitmapImageFileTypePNG,
        NSBitmapImageRep,
        NSCalibratedRGBColorSpace,
        NSColor,
        NSCompositingOperationSourceOver,
        NSGraphicsContext,
        NSImage,
        NSMakeRect,
    )

    logo = NSImage.alloc().initWithContentsOfFile_(os.path.abspath(src))
    if logo is None:
        raise SystemExit(f"could not read {src}")

    rep = NSBitmapImageRep.alloc(
    ).initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, CANVAS, CANVAS, 8, 4, True, False,
        NSCalibratedRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    tile = NSMakeRect(TILE_INSET, TILE_INSET,
                      CANVAS - 2 * TILE_INSET, CANVAS - 2 * TILE_INSET)
    NSColor.colorWithCalibratedRed_green_blue_alpha_(*PAPER, 1.0).set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        tile, CORNER, CORNER).fill()

    lw, lh = logo.size().width, logo.size().height
    fx, fy, fw, fh = MARK_CROP
    mx, my, mw, mh = fx * lw, fy * lh, fw * lw, fh * lh
    span = (CANVAS - 2 * TILE_INSET) * MARK_SPAN
    scale = span / mh
    dw, dh = mw * scale, mh * scale
    # NSImage source rects are bottom-left origin; the crop box is given
    # top-left like image editors, so flip y
    src_rect = NSMakeRect(mx, lh - my - mh, mw, mh)
    logo.drawInRect_fromRect_operation_fraction_(
        NSMakeRect((CANVAS - dw) / 2, (CANVAS - dh) / 2, dw, dh),
        src_rect, NSCompositingOperationSourceOver, 1.0)
    if dev:
        _draw_dev_badge()

    NSGraphicsContext.restoreGraphicsState()
    png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    png.writeToFile_atomically_(os.path.abspath(dst), True)


def main():
    args = [a for a in sys.argv[1:] if a != "--dev"]
    dev = "--dev" in sys.argv[1:]
    if len(args) != 2:
        raise SystemExit("usage: make_icns.py <logo.png> <out.icns> [--dev]")
    src, out = args
    workdir = os.path.dirname(os.path.abspath(out)) or "."
    iconset = os.path.join(workdir, "Sotto.iconset")
    master = os.path.join(workdir, "icon_master.png")
    os.makedirs(iconset, exist_ok=True)

    make_square_master(src, master, dev=dev)
    for size in SIZES:
        for mult, suffix in ((1, ""), (2, "@2x")):
            px = size * mult
            dst = os.path.join(iconset, f"icon_{size}x{size}{suffix}.png")
            subprocess.run(
                ["sips", "-z", str(px), str(px), master, "--out", dst],
                check=True, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
