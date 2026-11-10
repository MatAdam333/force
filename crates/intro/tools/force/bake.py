"""Bakes the Force splash's wordmark into assets/force.mask (embedded by src/intro/splash.rs).

    ~/venvs/pdf/bin/python tools/force/bake.py        # any Python with Pillow

The splash is designed on a 1200 x 840 canvas (120 x 40 cells of 10 x 21 px): `FORCE` in Archivo
Black (OFL 1.1, this directory), fitted to 560 px wide at most from size 190, italic by the affine
shear x' = x + 0.22 y - 95, anti-aliased. This writes that mask, cropped to its bounding box, with
the box's position on the canvas, and the twelve speed streaks of the reference animation (same
seed, positions relative to the box). Format, little-endian:

    b"FMSK", u16 width, u16 height, i16 x0, i16 y0, u8 streak count,
    per streak: i16 dy (from y0), i16 dend (from x0), u16 length, f32 delay,
    then the zlib-compressed mask rows (u8 coverage, width x height).
"""
import os
import random
import struct
import zlib

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "assets", "force.mask")
CW, CH = 1200, 840
SLANT = (1, 0.22, -95, 0, 1, 0)


def word_mask():
    size = 190
    while True:
        m = Image.new("L", (CW, CH), 0)
        f = ImageFont.truetype(os.path.join(HERE, "ArchivoBlack-Regular.ttf"), size)
        ImageDraw.Draw(m).text((CW / 2, 400), "FORCE", font=f, fill=255, anchor="ms")
        x0, _, x1, _ = m.getbbox()
        if x1 - x0 <= 560 or size < 20:
            break
        size = int(size * 0.94)
    return m.transform(m.size, Image.AFFINE, SLANT, resample=Image.BICUBIC), size


def streaks(x0, y0, y1):
    # the reference's hstreak_list(12, 17, x0 - 30, x0 + 40, y0 + 8, y1 - 8, 120, 300)
    random.seed(17)
    out = []
    for _ in range(12):
        y = random.randint(y0 + 8, y1 - 8)
        end = random.randint(x0 - 30, x0 + 40)
        length = random.randint(120, 300)
        delay = random.uniform(0, 0.15)
        random.uniform(0, 6.28)  # its flicker phase (unused by this animation)
        out.append((y - y0, end - x0, length, delay))
    return out


def main():
    m, size = word_mask()
    x0, y0, x1, y1 = m.getbbox()
    crop = m.crop((x0, y0, x1, y1))
    w, h = crop.size
    st = streaks(x0, y0, y1)
    blob = b"FMSK" + struct.pack("<HHhhB", w, h, x0, y0, len(st))
    for dy, dend, length, delay in st:
        blob += struct.pack("<hhHf", dy, dend, length, delay)
    blob += zlib.compress(crop.tobytes(), 9)
    with open(OUT, "wb") as fh:
        fh.write(blob)
    print(f"FORCE at size {size}: {w}x{h} at ({x0},{y0}) on the 1200x840 canvas, "
          f"{len(st)} streaks -> {os.path.normpath(OUT)} ({len(blob)} bytes)")


main()
