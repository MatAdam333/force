"""Packs rendered frames (NNNN.png from scene.py) into the intro's film file.

    blender -b --python blender/pack.py -- FRAMES_DIR OUT.film

Runs inside Blender for its numpy and image loading. Format (all integers little-endian),
decoded by src/intro/film.rs:

    b"FRCFILM1", u16 width, u16 height, u16 fps, u16 frame count, u8 chroma step
    per frame: u32 byte length, then a zlib stream of three planes, each the difference
    (mod 256) from the previous frame's plane (the first frame: from zero):
        Y  (width x height)              Y = R/4 + G/2 + B/4
        Co (width/2 x height/2)          Co = (R - B)/2 / step + 128/step
        Cg (width/2 x height/2)          Cg = (G/2 - R/4 - B/4) / step + 128/step
    Width and height are even. Chroma is the mean of each 2x2 block.
"""

import glob
import struct
import sys
import zlib

import bpy
import numpy as np

CHROMA_STEP = 2
FPS = 60  # as rendered by scene.py


def load(path):
    im = bpy.data.images.load(path)
    w, h = im.size
    px = np.empty(w * h * 4, dtype=np.float32)
    im.pixels.foreach_get(px)
    bpy.data.images.remove(im)
    # Blender stores images bottom-up as floats; the file holds display-referred sRGB
    return px.reshape(h, w, 4)[::-1, :, :3] * 255.0


def planes(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.25 * r + 0.5 * g + 0.25 * b
    co = 0.5 * (r - b)
    cg = 0.5 * g - 0.25 * (r + b)
    h, w = y.shape

    def quarter(c):
        return c.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))

    return [
        np.clip(np.round(y), 0, 255).astype(np.int16),
        np.clip(np.round(quarter(co) / CHROMA_STEP + 128 / CHROMA_STEP), 0, 255).astype(np.int16),
        np.clip(np.round(quarter(cg) / CHROMA_STEP + 128 / CHROMA_STEP), 0, 255).astype(np.int16),
    ]


def main():
    src, out = sys.argv[sys.argv.index("--") + 1 :][:2]
    files = sorted(glob.glob(glob.escape(src) + "/[0-9][0-9][0-9][0-9].png"))
    if not files:
        raise SystemExit(f"no frames in {src}")
    first = load(files[0])
    h, w = first.shape[:2]
    if w % 2 or h % 2:
        raise SystemExit("frame size must be even")
    fps = FPS
    blob = bytearray(b"FRCFILM1" + struct.pack("<HHHHB", w, h, fps, len(files), CHROMA_STEP))
    prev = None
    for f in files:
        cur = planes(load(f))
        base = prev if prev is not None else [np.zeros_like(p) for p in cur]
        body = b"".join(((p - q) & 0xFF).astype(np.uint8).tobytes() for p, q in zip(cur, base))
        z = zlib.compress(body, 9)
        blob += struct.pack("<I", len(z)) + z
        prev = cur
    with open(out, "wb") as fh:
        fh.write(blob)
    print(f"packed {len(files)} frames {w}x{h} -> {out} ({len(blob) / 1e6:.2f} MB)")


main()
