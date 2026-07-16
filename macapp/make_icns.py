# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Build an .icns app icon from the Sotto wordmark PNG.

The logo is a wide wordmark (1170x340), so it is first composited centered
onto a transparent square canvas (aspect preserved, with breathing room), then
scaled to every iconset size with sips and packed with iconutil. Uses AppKit
via pyobjc — no Pillow dependency.

Usage: python macapp/make_icns.py logo/sottoLogo.png build/Sotto.icns
"""
import os
import subprocess
import sys

CANVAS = 1024
INSET = 0.82  # logo occupies at most this fraction of the canvas
SIZES = [16, 32, 128, 256, 512]


def make_square_master(src: str, dst: str):
    from AppKit import (
        NSBitmapImageFileTypePNG,
        NSBitmapImageRep,
        NSCalibratedRGBColorSpace,
        NSCompositingOperationSourceOver,
        NSGraphicsContext,
        NSImage,
        NSMakeRect,
    )

    logo = NSImage.alloc().initWithContentsOfFile_(os.path.abspath(src))
    if logo is None:
        raise SystemExit(f"could not read {src}")
    w, h = logo.size().width, logo.size().height
    scale = (CANVAS * INSET) / max(w, h)
    dw, dh = w * scale, h * scale

    rep = NSBitmapImageRep.alloc(
    ).initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, CANVAS, CANVAS, 8, 4, True, False,
        NSCalibratedRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    logo.drawInRect_fromRect_operation_fraction_(
        NSMakeRect((CANVAS - dw) / 2, (CANVAS - dh) / 2, dw, dh),
        NSMakeRect(0, 0, w, h),
        NSCompositingOperationSourceOver, 1.0)
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
