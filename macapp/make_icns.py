# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Build an .icns app icon from the Sotto logo PNG.

The Dock tile is a macOS-style rounded square: the logo's warm-white paper
color fills a rounded rect, and the waveform mark (cropped from the left of
the wordmark — same background, so the crop blends seamlessly) sits centered.
Rendered once at 1024px, then scaled to every iconset size with sips and
packed with iconutil. Uses AppKit via pyobjc — no Pillow dependency.

Usage: python macapp/make_icns.py logo/sottoLogo.png build/Sotto.icns
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


def make_square_master(src: str, dst: str):
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

    NSGraphicsContext.restoreGraphicsState()
    png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    png.writeToFile_atomically_(os.path.abspath(dst), True)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: make_icns.py <logo.png> <out.icns>")
    src, out = sys.argv[1], sys.argv[2]
    workdir = os.path.dirname(os.path.abspath(out)) or "."
    iconset = os.path.join(workdir, "Sotto.iconset")
    master = os.path.join(workdir, "icon_master.png")
    os.makedirs(iconset, exist_ok=True)

    make_square_master(src, master)
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
