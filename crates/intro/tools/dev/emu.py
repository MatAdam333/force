"""Replays a force-intro output stream like a terminal and saves the screen after frame N (to see
what the encoder's tolerance and the quality levels actually leave on screen). Needs Pillow.

    python3 tools/dev/emu.py STREAM.bin COLS ROWS N OUT.png    (stream from fake_term.py --dump)
"""
import re, sys
from PIL import Image
path, cols, rows, frame_at, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
d = open(path, 'rb').read().decode('utf-8', 'replace')
frames = d.split('\x1b[?2026h')
scr = [[((0,0,0),(0,0,0))]*cols for _ in range(rows)]
fg, bg = (200,200,200), (0,0,0)
tok = re.compile(r'\x1b\[([0-9;?]*)([A-Za-z])|(.)', re.S)
for k, fr in enumerate(frames[1:frame_at+1]):
    cur = [0, 0]
    for m in tok.finditer(fr):
        if m.group(3) is None:
            p, f = m.group(1), m.group(2)
            nums = [int(x) for x in p.split(';') if x.isdigit()] if '?' not in p else []
            if f == 'H': cur = [nums[0]-1, nums[1]-1] if nums else [0, 0]
            elif f == 'C': cur[1] += nums[0] if nums else 1
            elif f == 'm':
                i = 0
                if not nums: fg, bg = (200,200,200), (0,0,0)
                while i < len(nums):
                    n = nums[i]
                    if n == 0: fg, bg = (200,200,200), (0,0,0)
                    elif n in (38, 48) and i+4 < len(nums)+1 and nums[i+1] == 2:
                        c = tuple(nums[i+2:i+5]); i += 4
                        if n == 38: fg = c
                        else: bg = c
                    elif n == 39: fg = (200,200,200)
                    elif n == 49: bg = (0,0,0)
                    i += 1
        else:
            ch = m.group(3)
            if ch in '\r\n': continue
            r, c = cur
            if 0 <= r < rows and 0 <= c < cols:
                scr[r][c] = {'▀': (fg, bg), '▄': (bg, fg)}.get(ch, (bg, bg))
            cur[1] += 1
img = Image.new('RGB', (cols*5, rows*12))
px = img.load()
for r in range(rows):
    for c in range(cols):
        t, b = scr[r][c]
        for y in range(12):
            for x in range(5):
                px[c*5+x, r*12+y] = t if y < 6 else b
img.save(out)
