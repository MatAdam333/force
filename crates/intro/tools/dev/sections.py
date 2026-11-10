"""Per-section frame rate and quality levels from a `force-intro --trace FILE` log
("t level fps bytes" per frame sent). The section times are the v11-v13 beats.

    python3 tools/dev/sections.py TRACE.txt
"""
import sys, collections
SECTIONS = [("walk", 0.0, 1.9), ("orbit (camera starts rotating)", 1.9, 3.64), ("sit, laptop", 3.64, 5.72),
            ("spiral into the screen", 5.72, 8.12), ("crossfade", 8.12, 8.62), ("splash", 8.62, 11.72)]
rows = [l.split() for l in open(sys.argv[1])]
for name, a, b in SECTIONS:
    fr = [(float(t), int(l), int(f), int(n)) for t, l, f, n in rows if a <= float(t) < b]
    if not fr: continue
    hist = collections.Counter(l for _, l, _, _ in fr)
    lv = " ".join(f"L{k}:{hist[k]*100//len(fr)}%" for k in sorted(hist))
    fps30 = sum(1 for _, _, f, _ in fr if f < 60) * 100 // len(fr)
    print(f"  {name:<32} {len(fr)/(b-a):5.1f} fps  {lv}  {'(30 fps for %d%%)' % fps30 if fps30 else ''}")
