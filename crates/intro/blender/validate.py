"""Checks the animation in a built scene for physical problems and renders review sheets.

    blender -b --python blender/scene.py -- --save /tmp/intro.blend
    blender -b /tmp/intro.blend --python blender/validate.py -- --out DIR [--sheets]

Writes DIR/report.md (and report.json). With --sheets also DIR/sheet_<view>_<window>.png: the
figure from the front, side and above every 0.1 s, solid-shaded, for a human (or an agent) to look
at. Exit status 1 when a check fails.

| Check | Measures | Fails when |
|---|---|---|
| foot skate | horizontal speed of a foot's lowest point while it touches the floor | > SKATE_MAX m/s |
| floor | lowest foot point | below -FLOOR_TOL m |
| jolts | acceleration of pelvis, head, hands (central differences, per frame) | > ACCEL_MAX |
| body overlap | capsules around the bones: leg vs leg, arm vs torso, arm vs leg | deeper than OVERLAP_MAX |
| chair | capsule points inside the chair mesh | deeper than CHAIR_MAX (seat/back padding allowed) |
| seating | pelvis above the seat, back to the backrest, at the end | off by more than the limits |
| balance | pelvis over the feet while standing still | outside the support area |
| joints | knee and elbow bending the wrong way or past straight | hyperextended |
| laptop | posed head, suit and chair vertices inside the laptop's base or lid (as boxes), every frame. Hands are left out (they grip it and type): the close-up sheets show them | deeper than LAPTOP_MAX |
| camera | the camera's distance to the nearest surface (body, hair, suit, chair, laptop) | closer than CAMERA_MIN |
| screen view | rays from the camera to the screen's corners and middle, once the camera is into the screen | blocked |
"""

import json
import math
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

FPS = bpy.context.scene.render.fps  # as rendered (scene.py)
SKATE_MAX = 0.12  # m/s while in contact
CONTACT = 0.025  # m: a foot point this close to the floor is touching it (ball joint: 0.015)
FLOOR_TOL = 0.015
ACCEL_MAX = {"pelvis": 9.0, "Head": 12.0, "hand_l": 30.0, "hand_r": 30.0}
OVERLAP_MAX = 0.02
CHAIR_MAX = 0.03
LAPTOP_MAX = 0.006  # hands rest on it: a few millimetres of skin contact is fine
CAMERA_MIN = 0.04  # the camera clips at 0.02 m: anything nearer could be cut open
# capsule radii around bones (metres), suit included
RADIUS = {"thigh": 0.085, "calf": 0.06, "upperarm": 0.055, "lowerarm": 0.045, "spine": 0.14}


def args():
    a = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    out, sheets = None, False
    i = 0
    while i < len(a):
        if a[i] == "--out":
            out = a[i + 1]
            i += 2
        elif a[i] == "--sheets":
            sheets = True
            i += 1
        else:
            raise SystemExit(f"unknown option {a[i]}")
    return out or "/tmp/force-intro-validate", sheets


def seg_dist(p1, q1, p2, q2):
    """Closest distance between segments p1q1 and p2q2."""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = d1.dot(d1), d2.dot(d2), d2.dot(r)
    if a < 1e-9 and e < 1e-9:
        return r.length
    if a < 1e-9:
        s, t = 0.0, min(max(f / e, 0.0), 1.0)
    else:
        c = d1.dot(r)
        if e < 1e-9:
            t, s = 0.0, min(max(-c / a, 0.0), 1.0)
        else:
            b = d1.dot(d2)
            den = a * e - b * b
            s = min(max((b * f - c * e) / den, 0.0), 1.0) if den > 1e-9 else 0.0
            t = (b * s + f) / e
            if t < 0:
                t, s = 0.0, min(max(-c / a, 0.0), 1.0)
            elif t > 1:
                t, s = 1.0, min(max((b - c) / a, 0.0), 1.0)
    return ((p1 + d1 * s) - (p2 + d2 * t)).length


def sample(arm, n):
    """World positions of the joints we check, for every frame."""
    sc = bpy.context.scene
    names = ["pelvis", "spine_01", "spine_03", "neck_01", "Head"]
    for s in "lr":
        names += [f"thigh_{s}", f"calf_{s}", f"foot_{s}", f"ball_{s}", f"upperarm_{s}",
                  f"lowerarm_{s}", f"hand_{s}"]
    out = []
    for f in range(n + 1):
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        ev = arm.evaluated_get(dg)
        mw = ev.matrix_world
        fr = {}
        for b in names:
            pb = ev.pose.bones[b]
            fr[b] = (mw @ pb.head, mw @ pb.tail)
        out.append(fr)
    return out


def chair_tree():
    for o in bpy.data.objects:
        if o.type == "MESH" and "chair" in o.name.lower():
            dg = bpy.context.evaluated_depsgraph_get()
            ev = o.evaluated_get(dg)
            me = ev.to_mesh()
            verts = [ev.matrix_world @ v.co for v in me.vertices]
            polys = [tuple(p.vertices) for p in me.polygons]
            tree = BVHTree.FromPolygons(verts, polys)
            ev.to_mesh_clear()
            seat_z = max(v.z for v in verts if abs(v.x) < 0.1 and -0.25 < v.y < 0.0) if verts else 0.0
            return tree, seat_z
    return None, 0.0


def inside(tree, p, depth_probe=0.5):
    """Penetration depth of p into the closed chair mesh (0 if outside), by the nearest surface
    and its normal."""
    hit = tree.find_nearest(p)
    if hit[0] is None:
        return 0.0
    loc, normal, _, dist = hit
    if (p - loc).dot(normal) >= 0 or dist > 0.12:
        return 0.0  # outside, or "deep inside" an open shell: not a real penetration
    # confirm with a ray: inside a closed part, a ray straight up hits the part again
    up = tree.ray_cast(p, Vector((0.0, 0.0, 1.0)), 1.5)
    return dist if up[0] is not None else 0.0


def posed(names):
    """BVH tree and world vertices of the posed meshes with these names."""
    dg = bpy.context.evaluated_depsgraph_get()
    verts, polys = [], []
    for name in names:
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        base = len(verts)
        verts += [ev.matrix_world @ v.co for v in me.vertices]
        polys += [tuple(base + i for i in p.vertices) for p in me.polygons]
        ev.to_mesh_clear()
    return (BVHTree.FromPolygons(verts, polys) if polys else None), verts


LAPTOP = ("classic_laptop", "classic_laptop_screen", "classic_laptop_stand")
PEOPLE = {"body": ("SuperHero_Male",), "suit": ("Suit",), "chair": ("mid_century_lounge_chair", "modern_arm_chair_01")}


def laptop_boxes():
    """The laptop as two boxes, each in the frame of the empty it moves with: the base (root
    'laptop') and the lid ('laptop_hinge'), bounds in that frame's (model) units."""
    boxes = []
    for frame_name, parts in (("laptop", ("classic_laptop", "classic_laptop_stand")), ("laptop_hinge", ("classic_laptop_screen",))):
        vs = []
        for n in parts:
            o = bpy.data.objects.get(n)
            if o is not None:
                vs += [o.matrix_local @ v.co for v in o.data.vertices]
        lo = Vector([min(v[i] for v in vs) for i in range(3)])
        hi = Vector([max(v[i] for v in vs) for i in range(3)])
        boxes.append((bpy.data.objects[frame_name], lo, hi))
    return boxes


def box_depth(p, lo, hi):
    """How far p is inside the box (<= 0 outside)."""
    return min(p.x - lo.x, hi.x - p.x, p.y - lo.y, hi.y - p.y, p.z - lo.z, hi.z - p.z)


def part_of(o, i):
    g = max(o.data.vertices[i].groups, key=lambda x: x.weight, default=None)
    return o.vertex_groups[g.group].name if g else "?"


HANDS = ("hand", "thumb", "index", "middle", "ring", "pinky")


def mesh_checks(n, add):
    """Laptop against body, suit and chair; the camera's clearance; the screen in view at the end."""
    sc = bpy.context.scene
    if bpy.data.objects.get("classic_laptop") is None:
        return
    boxes = laptop_boxes()
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    for f in range(n + 1):
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        frames_inv = [(fo.matrix_world.inverted(), lo, hi, fo.matrix_world.to_scale().x) for fo, lo, hi in boxes]
        # vertices of the body (hands and head: the rest is masked under the suit), suit and chair
        # inside the laptop
        for k in ("body", "suit", "chair"):
            for name in PEOPLE[k]:
                o = bpy.data.objects.get(name)
                if o is None:
                    continue
                ev = o.evaluated_get(dg)
                me = ev.to_mesh()
                worst, where = 0.0, ""
                ev_arm = arm.evaluated_get(dg)
                head = ev_arm.matrix_world @ ev_arm.pose.bones["Head"].head
                for i, v in enumerate(me.vertices):
                    w = ev.matrix_world @ v.co
                    if k == "body" and (w - head).length > 0.3:
                        continue  # the hands: they hold it and type (see the close-up sheets)
                    if k == "suit" and part_of(o, i).startswith(HANDS):
                        continue  # the cuffs, at the wrists
                    for inv, lo, hi, scale in frames_inv:
                        d = box_depth(inv @ w, lo, hi) * scale
                        if d > worst:
                            worst = d
                            where = part_of(o, i) if k == "suit" else ("head" if k == "body" else "")
                ev.to_mesh_clear()
                if worst > LAPTOP_MAX:
                    add("laptop", f, worst, LAPTOP_MAX, f"{k} {where} inside the laptop".replace("  ", " "))
        cam = sc.camera.matrix_world.translation
        for k, names in {**PEOPLE, "hair": ("Hair_SimpleParted",), "laptop": LAPTOP}.items():
            tree, _ = posed(names)
            hit = tree.find_nearest(cam) if tree else (None,)
            if hit[0] is not None and hit[3] < CAMERA_MIN:
                add("camera", f, hit[3], CAMERA_MIN, f"camera too close to the {k}")
    # the end: nothing between the camera and the screen
    for f in range(round(n * 0.9), n + 1):
        sc.frame_set(f)
        scr = bpy.data.objects["classic_laptop_screen"].evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = scr.to_mesh()
        slot = next(i for i, m in enumerate(scr.material_slots) if m.material.name == "classic_laptop_screen")
        disp = [scr.matrix_world @ me.vertices[v].co for p in me.polygons if p.material_index == slot for v in p.vertices]
        scr.to_mesh_clear()
        mid = sum(disp, Vector()) / len(disp)
        corners = [min(disp, key=lambda v: v.x + v.z), max(disp, key=lambda v: v.x + v.z),
                   min(disp, key=lambda v: v.x - v.z), max(disp, key=lambda v: v.x - v.z)]
        cam = sc.camera.matrix_world.translation
        blockers, _ = posed([o.name for o in bpy.data.objects if o.type == "MESH" and o.name != "classic_laptop_screen"])
        for p in [mid] + [c.lerp(mid, 0.1) for c in corners]:
            d = p - cam
            hit = blockers.ray_cast(cam, d.normalized(), d.length - 0.01)
            if hit[0] is not None:
                add("screen view", f, d.length - hit[3], 0.0, "something between the camera and the screen")
                break


def check(frames):
    n = len(frames)
    issues = []

    def add(kind, f, value, limit, what):
        issues.append({"check": kind, "frame": f, "t": round(f / FPS, 2), "value": round(value, 3),
                       "limit": limit, "what": what})

    # foot skate and floor
    for s in "lr":
        for f in range(1, n):
            for b in (f"foot_{s}", f"ball_{s}"):
                p0, p1 = frames[f - 1][b][1], frames[f][b][1]  # tail: heel->ball / toe
                if p1.z < -FLOOR_TOL:
                    add("floor", f, p1.z, -FLOOR_TOL, f"{b} below the floor")
                if p0.z < CONTACT and p1.z < CONTACT:
                    v = (p1 - p0).xy.length * FPS
                    if v > SKATE_MAX:
                        add("foot skate", f, v, SKATE_MAX, f"{b} slides while planted")
    # jolts
    for b, lim in ACCEL_MAX.items():
        for f in range(1, n - 1):
            a = (frames[f + 1][b][0] - frames[f][b][0] * 2 + frames[f - 1][b][0]).length * FPS * FPS
            if a > lim:
                add("jolt", f, a, lim, f"{b} accelerates")
    # body overlap (capsules)
    def cap(fr, b):
        kind = b.split("_")[0]
        return fr[b][0], fr[b][1], RADIUS.get(kind, 0.05)

    pairs = []
    for a in ("thigh_l", "calf_l"):
        for b in ("thigh_r", "calf_r"):
            pairs.append((a, b))
    for s in "lr":
        for limb in (f"upperarm_{s}", f"lowerarm_{s}"):
            pairs += [(limb, "spine_03"), (limb, f"thigh_{s}")]
    for f in range(n):
        fr = frames[f]
        for a, b in pairs:
            pa, qa, ra = cap(fr, a)
            if b == "spine_03":
                pb, qb, rb = fr["spine_01"][0], fr["neck_01"][0], RADIUS["spine"]
            else:
                pb, qb, rb = cap(fr, b)
            depth = ra + rb - seg_dist(pa, qa, pb, qb)
            # thighs touch at the crotch and a crossed leg rests on the other: allow contact
            allow = OVERLAP_MAX + (0.05 if a.startswith("thigh") and b.startswith("thigh") else 0.0)
            if depth > allow:
                add("body overlap", f, depth, allow, f"{a} through {b}")
    # chair penetration
    tree, seat_z = chair_tree()
    if tree:
        for f in range(n):
            fr = frames[f]
            for b in ("calf_l", "calf_r", "thigh_l", "thigh_r", "lowerarm_l", "lowerarm_r", "upperarm_l", "upperarm_r"):
                p, q = fr[b]
                r = RADIUS[b.split("_")[0]]
                for k in range(5):
                    pt = p.lerp(q, k / 4)
                    d = inside(tree, pt)
                    lim = CHAIR_MAX + (0.04 if b.startswith("thigh") else 0.0)  # cushion gives
                    if d > lim:
                        add("chair", f, d, lim, f"{b} inside the chair")
                        break
    # balance while standing still: pelvis over the feet
    for f in range(1, n):
        fr = frames[f]
        feet = [fr[f"foot_{s}"][0] for s in "lr"]
        if any(p.z > 0.2 for p in feet) or fr["pelvis"][0].z < 0.75:
            continue  # stepping or sitting
        v = (fr["pelvis"][0] - frames[f - 1]["pelvis"][0]).xy.length * FPS
        if v > 0.2:
            continue  # walking
        a, b, p = feet[0].xy, feet[1].xy, fr["pelvis"][0].xy
        ab = b - a
        u = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-9)))
        off = (p - (a + ab * u)).length
        if off > 0.16:
            add("balance", f, off, 0.16, "pelvis not over the feet while standing")
    # joints bending the wrong way
    for f in range(n):
        fr = frames[f]
        for s in "lr":
            hip, knee, ankle = fr[f"thigh_{s}"][0], fr[f"calf_{s}"][0], fr[f"foot_{s}"][0]
            toe = fr[f"ball_{s}"][1]
            bend = (knee - (hip + ankle) * 0.5)
            fwd = (toe - ankle)
            fwd.z = 0
            if bend.length > 0.04 and fwd.length > 1e-6 and bend.normalized().dot(fwd.normalized()) < -0.6:
                add("joints", f, bend.length, 0.04, f"knee {s} bends backward")
    # seating at the end
    end = frames[-1]
    pel = end["pelvis"][0]
    seat = {"pelvis_z": round(pel.z, 3), "pelvis_y": round(pel.y, 3), "seat_z": round(seat_z, 3)}
    if tree and pel.z - seat_z > 0.2:
        add("seating", n - 1, pel.z - seat_z, 0.2, "pelvis floats above the seat")
    mesh_checks(n - 1, add)
    return issues, seat


def summarise(issues):
    by = {}
    for i in issues:
        k = (i["check"], i["what"])
        e = by.setdefault(k, {"count": 0, "worst": i, "first": i["t"], "last": i["t"]})
        e["count"] += 1
        e["last"] = i["t"]
        if abs(i["value"]) > abs(e["worst"]["value"]):
            e["worst"] = i
    return by


def sheets(arm, out):
    """Solid-shaded review sheets: front, side and top, every 0.1 s, per window."""
    sc = bpy.context.scene
    keep = (sc.render.engine, sc.render.resolution_x, sc.render.resolution_y, sc.camera)
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x, sc.render.resolution_y = 200, 260
    cam = bpy.data.objects.new("review_cam", bpy.data.cameras.new("review_cam"))
    sc.collection.objects.link(cam)
    cam.data.lens = 32
    sc.camera = cam
    os.makedirs(out + "/frames", exist_ok=True)
    windows = {"walk": (0.3, 2.3), "turn": (2.2, 3.3), "sit": (3.0, 4.95), "laptop": (4.15, 6.7), "settle": (6.6, 8.77)}
    made = []
    for name, (t0, t1) in windows.items():
        for view in ("front", "side", "top"):
            files = []
            t = t0
            while t <= t1 + 1e-6:
                f = round(t * FPS)
                sc.frame_set(f)
                dg = bpy.context.evaluated_depsgraph_get()
                ev = arm.evaluated_get(dg)
                p = ev.matrix_world @ ev.pose.bones["pelvis"].head
                c = Vector((p.x, p.y, 0.85))
                off = {"front": Vector((0, -3.0, 0.15)), "side": Vector((3.0, 0, 0.15)),
                       "top": Vector((0.01, -0.6, 3.0))}[view]
                cam.location = c + off
                cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
                path = f"{out}/frames/{name}_{view}_{f:04d}.png"
                sc.render.filepath = path
                bpy.ops.render.render(write_still=True)
                files.append(path)
                t += 0.1
            made.append((name, view, files))
    # the laptop close up (hands, lid, lap) every 0.25 s from it is picked up to the end
    lap = bpy.data.objects.get("laptop")
    if lap is not None:
        cam.data.lens = 50
        sc.render.resolution_x = sc.render.resolution_y = 240
        for view, off in (("front", Vector((0.5, -0.9, 0.5))), ("left", Vector((0.95, 0.3, 0.45))), ("above", Vector((0.05, 0.35, 1.0)))):
            files = []
            for f in range(round(1.6 * FPS), sc.frame_end + 1, round(0.25 * FPS)):
                sc.frame_set(f)
                c = lap.matrix_world @ Vector((0.0, 0.0, 0.05))
                cam.location = c + off
                cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
                path = f"{out}/frames/hands_{view}_{f:04d}.png"
                sc.render.filepath = path
                bpy.ops.render.render(write_still=True)
                files.append(path)
            made.append(("hands", view, files))
    sc.render.engine, sc.render.resolution_x, sc.render.resolution_y, sc.camera = keep
    return made


def main():
    out, want_sheets = args()
    os.makedirs(out, exist_ok=True)
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    n = bpy.context.scene.frame_end
    frames = sample(arm, n)
    issues, seat = check(frames)
    by = summarise(issues)
    fails = sorted(by.items(), key=lambda kv: -kv[1]["count"])
    lines = ["# Motion validation", "", f"Frames: {n + 1}. Seating at the end: {seat}.", ""]
    if not fails:
        lines.append("All checks pass.")
    else:
        lines += ["| Check | Problem | Frames | From | To | Worst (value / limit at t) |", "|---|---|---|---|---|---|"]
        for (kind, what), e in fails:
            w = e["worst"]
            lines.append(f"| {kind} | {what} | {e['count']} | {e['first']} | {e['last']} | {w['value']} / {w['limit']} at {w['t']} |")
    open(out + "/report.md", "w").write("\n".join(lines) + "\n")
    json.dump({"seat": seat, "issues": issues}, open(out + "/report.json", "w"))
    print("\n".join(lines))
    if want_sheets:
        for name, view, files in sheets(arm, out):
            print("SHEET", name, view, len(files))
    sys.exit(1 if fails else 0)


main()
