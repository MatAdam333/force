"""Builds and renders the force intro scene in Blender (headless).

    blender -b --python blender/scene.py -- [--frames A-B] [--res WxH] [--samples N]
                                            [--out DIR] [--jobs N] [--save FILE.blend]

Assets (all CC0, see ../ASSETS.md) are read from $FORCE_INTRO_ASSETS
(default ~/force-intro-assets):
  ubc/   Quaternius Universal Base Characters (Superhero_Male, Hair_SimpleParted, eyebrows)
  ual/   Quaternius Universal Animation Library (same 65-bone rig: no retargeting)
  mid_century_lounge_chair/  Poly Haven lounge chair (glTF, 2k textures)
  classic_laptop/  Poly Haven classic laptop (glTF, 2k textures)
  armchair/  Poly Haven modern_arm_chair_01 (optional, the earlier chair)

Scene: chair at the origin facing -Y. The motion and camera path are the classic procedural ones
(footstep walk, three-step turn, sit-down in beats, leaning back), exported per frame by the `motion/`
crate into motion/classic.jsonl and mapped onto this rig. He carries a laptop, puts it on his lap and
opens it; the camera then leaves the classic path and zooms into its screen. The intro's fades and grain are added at
playback, so the frames here are clean.
"""

import math
import os
import subprocess
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mathutils import Euler, Matrix, Quaternion, Vector

ASSETS = os.environ.get("FORCE_INTRO_ASSETS", os.path.expanduser("~/force-intro-assets"))
UBC = ASSETS + "/ubc/Universal Base Characters[Standard]"
UAL = ASSETS + "/ual/Universal Animation Library[Standard]/Unreal-Godot/UAL1_Standard.glb"
CHAIR = ASSETS + "/mid_century_lounge_chair/mid_century_lounge_chair_2k.gltf"
LAPTOP = ASSETS + "/classic_laptop/classic_laptop_2k.gltf"
# the player draws the splash for the laptop's screen (build it first: cargo build --release)
PLAYER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "target", "release", "force-intro")
SPLASH_TEXTURE = ASSETS + "/splash/screen.png"
TONE_LUT = ASSETS + "/splash/tone_inverse.npy"  # measured once (delete it after changing the look)
SCREEN_GLOW = 1.0  # emission strength: the splash's colours come out as the player draws them

FPS = 60
TOTAL = 10.0
CLIP_FPS = 24  # the animation library is authored at 24 fps

# ---- motion ---------------------------------------------------------------------------------
# The classic procedural motion from the earlier real-time version (footstep walk, three-step turn,
# sit-down in beats, getting comfortable), exported per frame by `motion/` (a small Rust crate built
# from that code) into motion/classic.jsonl, together with its camera path. Shot timing lives there:
# `motion/src/timing.rs`, poses in `motion/src/poses.rs`. Regenerate the file with
#     cargo run --release --manifest-path motion/Cargo.toml > motion/classic.jsonl
MOTION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "motion", "classic.jsonl")
CHAIR_SCALE = 1.0
CUSHION_GIVE = 0.09  # metres: how deep he may press into the chair (the dent's reach)
CUSHION_MARGIN = -0.004  # pressed points end this far inside him (hidden under the suit)
CHAIR_SHAPE = (1.12, 0.92, 0.85)  # x, y, z: at its own size the chair is a little tall, deep and
# narrow for him (his knees could not clear its front edge): wider, shallower, lower
LAPTOP_SCALE = 0.5  # the model is twice the size of a real laptop
# laptop beats (seconds): carried at his side in the left hand; brought in front of him as he turns
# (so it stays clear of the armrest), held there while he sits, put down on his thighs as he lands;
# the right hand lifts the lid; the camera then goes round behind him and into the screen
LAPTOP_BEATS = {
    "to_front": (1.65, 2.7),  # from his side round to in front of his hips, during the turn
    "to_lap": (4.16667, 4.81667),  # down onto his thighs as he lands
    "reach_lid": (4.76667, 5.21667),  # right hand from the armrest to the lid's edge
    "open": (5.21667, 5.91667),  # lifts the lid
    "rest_hand": (5.91667, 6.51667),
    "screen_on": (5.31667, 6.11667),  # the screen lights up as the lid opens  # right hand comes to rest on the palm rest
}
CAMERA_TAKEOVER = 5.71667  # from here the camera leaves the classic path: one move round him and into the screen
SCREEN_FILL_AT = 8.11667  # the screen fills the frame (the player's splash then takes over)
CAMERA_END_OFFSET = (0.0, -0.1, 0.03)  # metres from his eyes: just in front of his face, square to the screen
CAMERA_RISE = 0.4  # the spiral rises this much at its middle, clear of the chair's back
# The shots were composed for 2.39:1: the exported fields of view are vertical ones at that aspect.
# The film is rendered taller (16:10) with the same horizontal view, so it shows more above and
# below, and the player crops it to fill any terminal (16:9 full screen shows nearly all of it).
DESIGN_ASPECT = 2.39
# old scene: Y up, the chair at (-1.2, 0, 0) facing +X, his right +Z. Here: Z up, the chair at the
# origin facing -Y, his left +X.
OLD_CHAIR = Vector((-1.2, 0.0, 0.0))
ANKLE_LIFT = 0.016  # this rig's ankle sits higher above a flat sole than the old skeleton's
SEAT_LIFT = 0.015  # seated, the body is lifted this much onto the cushion


# ---- body shape -----------------------------------------------------------------------------
# The free base body has superhero proportions; pull muscle mass toward the bones (fraction of
# the distance) to get an athletic, lean build.
SLIM = {
    "upperarm": 0.24,
    "lowerarm": 0.16,
    "clavicle": 0.18,
    "spine_03": 0.14,
    "spine_02": 0.10,
    "neck_01": 0.08,
    "thigh": 0.04,
    "calf": 0.03,
}

# ---- palette (linear RGB) -------------------------------------------------------------------
SUIT = (0.011, 0.012, 0.016)
SHIRT = (0.62, 0.62, 0.60)
TIE = (0.20, 0.018, 0.026)
SHOES = (0.012, 0.010, 0.009)
HAIR = (0.045, 0.030, 0.022)
LEATHER_TINT = (1.6, 0.30, 0.24)  # the chair leather's brightness times this: burgundy


def args():
    a = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    opts = {"frames": None, "res": (320, 200), "samples": 64, "out": None, "save": None, "jobs": 8}
    i = 0
    while i < len(a):
        k = a[i]
        v = a[i + 1] if i + 1 < len(a) else None
        if k == "--frames":
            s, e = v.split("-")
            opts["frames"] = (int(s), int(e))
        elif k == "--res":
            w, h = v.split("x")
            opts["res"] = (int(w), int(h))
        elif k == "--samples":
            opts["samples"] = int(v)
        elif k == "--out":
            opts["out"] = v
        elif k == "--save":
            opts["save"] = v
        elif k == "--jobs":
            opts["jobs"] = max(1, int(v))
        else:
            raise SystemExit(f"unknown option {k}")
        i += 2
    return opts


def smoothstep(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def imported(fn):
    """Runs an importer and returns the objects it created."""
    before = set(bpy.data.objects)
    fn()
    return [o for o in bpy.data.objects if o not in before]


# ---- character --------------------------------------------------------------------------------


def load_character():
    objs = imported(
        lambda: bpy.ops.import_scene.gltf(
            filepath=UBC + "/Base Characters/Godot - UE/Superhero_Male_FullBody.gltf"
        )
    )
    for o in objs:
        if o.name.startswith("Icosphere"):
            bpy.data.objects.remove(o)
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    body = bpy.data.objects["SuperHero_Male"]
    hair_objs = imported(
        lambda: bpy.ops.import_scene.gltf(
            filepath=UBC
            + "/Hairstyles/Rigged to Head Bone/glTF (Godot -Unreal)/Hair_SimpleParted.gltf"
        )
    )
    # the hair comes with its own copy of the rig: move it onto ours
    hair = next(o for o in hair_objs if o.type == "MESH" and o.name.startswith("Hair"))
    for o in hair_objs:
        if o is not hair:
            bpy.data.objects.remove(o)
    hair.parent = arm
    for m in hair.modifiers:
        if m.type == "ARMATURE":
            m.object = arm
    return arm, body, hair


def dominant_group(obj, v):
    if not v.groups:
        return ""
    g = max(v.groups, key=lambda g: g.weight)
    return obj.vertex_groups[g.group].name


def slim_body(arm, body):
    """Pulls each vertex toward its dominant bone by SLIM[...] (rest pose)."""
    bones = arm.data.bones
    mw = arm.matrix_world
    for v in body.data.vertices:
        name = dominant_group(body, v)
        key = next((k for k in SLIM if name.startswith(k)), None)
        if key is None or name not in bones:
            continue
        b = bones[name]
        a, t = mw @ b.head_local, mw @ b.tail_local
        p = body.matrix_world @ v.co
        ab = t - a
        u = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
        c = a + ab * u
        v.co = body.matrix_world.inverted() @ (p + (c - p) * SLIM[key])


SKIN_GROUPS = ("Head", "hand", "thumb", "index", "middle", "ring", "pinky")
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
HEM = 0.885  # rest-pose height of the jacket hem
NECK_CUT = 1.565  # rest-pose height where the shirt collar ends
COLLAR = 1.515  # collar band starts here (around the neck only)
WRIST_CUT = 0.728  # rest-pose |x| where the sleeves end (T-pose)
CUFF = 0.70  # shirt cuff shows from here


def make_suit(arm, body):
    """Builds the suit from a copy of the body: removes head and hands, irons out the muscle
    definition, inflates it, and paints regions (jacket, trousers, shirt, tie, collar, cuffs,
    shoes) into a colour attribute in the rest pose, so they stay on the cloth when it moves."""
    import bmesh

    suit = body.copy()
    suit.data = body.data.copy()
    suit.name = "Suit"
    bpy.context.collection.objects.link(suit)
    me = suit.data
    bm = bmesh.new()
    bm.from_mesh(me)
    deform = bm.verts.layers.deform.active
    names = {i: g.name for i, g in enumerate(suit.vertex_groups)}

    def dom(v):
        d = v[deform]
        if not d:
            return ""
        return names[max(d.items(), key=lambda kv: kv[1])[0]]

    mw = suit.matrix_world
    # cut straight across: the collar at a fixed height up the neck, the sleeves (T-pose: arms
    # along X) just past the wrist, so the shirt collar and cuffs get clean edges
    kill = []
    for v in bm.verts:
        p = mw @ v.co
        if p.z > NECK_CUT or abs(p.x) > WRIST_CUT:
            kill.append(v)
    bmesh.ops.delete(bm, geom=kill, context="VERTS")
    # the body is split along its texture seams; welded, the cloth has no cracks there
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    # iron out the muscle definition: cloth drapes over it
    for _ in range(14):
        bmesh.ops.smooth_vert(
            bm, verts=[v for v in bm.verts if not v.is_boundary], factor=0.5,
            use_axis_x=True, use_axis_y=True, use_axis_z=True,
        )
    bm.normal_update()
    # garments per vertex, from rest-pose positions (he faces -Y; his left is +X)
    garment = {}
    for v in bm.verts:
        n = dom(v)
        p = mw @ v.co
        if n.startswith(("foot", "ball")):
            g = "shoes"
        elif abs(p.x) > CUFF:
            g = "cuff"
        elif p.z > COLLAR and abs(p.x) < 0.08:
            g = "collar"
        elif n.startswith(("thigh", "calf")) or (n == "pelvis" and p.z < 0.88):
            g = "trousers"
        else:
            g = "jacket"
        garment[v.index] = g
    # inflate: the jacket stands off the body more than the trousers, so the hem shows
    push = {"jacket": 0.021, "collar": 0.012, "cuff": 0.008, "trousers": 0.018, "shoes": 0.012}
    rest = {}
    for v in bm.verts:
        rest[v.index] = (mw @ v.co).copy()
        v.co += v.normal * push[garment[v.index]]
    bm.to_mesh(me)
    bm.free()
    # the shader draws the shirt V, tie, lapels, collar, cuffs and shoes from these attributes,
    # so their edges are crisp and smooth instead of following the mesh's faces
    ra = me.attributes.new("rest", "FLOAT_VECTOR", "POINT")
    for i, v in enumerate(me.vertices):
        ra.data[i].vector = rest[v.index] if v.index in rest else v.co
    for g in ("collar", "cuff", "shoes"):
        at = me.attributes.new(g, "FLOAT", "POINT")
        for i in range(len(me.vertices)):
            at.data[i].value = 1.0 if garment.get(i) == g else 0.0
    me.materials.clear()
    me.materials.append(suit_material())
    for poly in me.polygons:
        poly.use_smooth = True
    # the body under the suit only shows at the head and hands
    vg = body.vertex_groups.new(name="visible_skin")
    for v in body.data.vertices:
        n = dominant_group(body, v)
        z = (body.matrix_world @ v.co).z
        x = abs((body.matrix_world @ v.co).x)
        if n == "Head" or z > NECK_CUT - 0.03 or x > WRIST_CUT - 0.02:
            vg.add([v.index], 1.0, "REPLACE")
    mask = body.modifiers.new("hide under suit", "MASK")
    mask.vertex_group = "visible_skin"
    body.modifiers.move(len(body.modifiers) - 1, 0)
    return suit


def principled(name, color, rough, sheen=0.0, spec=0.5):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Specular IOR Level"].default_value = spec
    if sheen:
        b.inputs["Sheen Weight"].default_value = sheen
    return m, b


def suit_material():
    """One material for the whole outfit: dark wool suit, white shirt opening and collar and cuffs,
    burgundy tie, satin-faced lapels, polished shoes. Regions come from the rest-pose position
    attribute (smooth edges) and the per-vertex garment attributes."""
    m, b = principled("M_suit", SUIT, 0.8, sheen=0.08)
    nt = m.node_tree
    N, L = nt.nodes, nt.links

    def node(kind, **inputs):
        n = N.new(kind)
        for k, v in inputs.items():
            if k == "op":
                n.operation = v
            elif k == "clamp":
                n.use_clamp = v
            else:
                n.inputs[k].default_value = v
        return n

    def math(op, a, b=None, clamp=False):
        n = node("ShaderNodeMath", op=op, clamp=clamp)
        for i, x in enumerate((a, b)):
            if x is None:
                continue
            if isinstance(x, (int, float)):
                n.inputs[i].default_value = x
            else:
                L.new(x, n.inputs[i])
        return n.outputs[0]

    def step(x, edge, soft, rising=True):
        """Smooth 0->1 as x crosses edge (over +-soft); falling if not rising."""
        n = N.new("ShaderNodeMapRange")
        n.interpolation_type = "SMOOTHSTEP"
        L.new(x, n.inputs["Value"])
        lo, hi = edge - soft, edge + soft
        n.inputs["From Min"].default_value = lo
        n.inputs["From Max"].default_value = hi
        n.inputs["To Min"].default_value = 0.0 if rising else 1.0
        n.inputs["To Max"].default_value = 1.0 if rising else 0.0
        return n.outputs["Result"]

    def attr(name, vec=False):
        n = N.new("ShaderNodeAttribute")
        n.attribute_name = name
        return n.outputs["Vector" if vec else "Fac"]

    sep = N.new("ShaderNodeSeparateXYZ")
    L.new(attr("rest", True), sep.inputs[0])
    x, y, z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
    ax = math("ABSOLUTE", x)
    front = step(y, -0.03, 0.02, rising=False)
    # V opening: from the collar down to the button at z 1.16, half-width 0.085 at z 1.50
    k = math("DIVIDE", math("SUBTRACT", z, 1.16), 0.34, clamp=True)
    v_half = math("MULTIPLY", k, 0.085)
    in_v = math("MULTIPLY", math("MULTIPLY", front, step(math("SUBTRACT", ax, v_half), 0.0, 0.004, rising=False)),
                step(z, 1.17, 0.006))
    lapel_half = math("MULTIPLY", k, 0.135)
    in_lapel = math("SUBTRACT",
                    math("MULTIPLY", math("MULTIPLY", front, step(math("SUBTRACT", ax, lapel_half), 0.0, 0.004, rising=False)),
                         math("MULTIPLY", step(z, 1.17, 0.006), step(z, 1.50, 0.006, rising=False))),
                    in_v, clamp=True)
    tie_half = math("ADD", 0.016, math("MULTIPLY", math("SUBTRACT", 1.0, k), 0.006))
    in_tie = math("MULTIPLY", math("MULTIPLY", in_v, step(math("SUBTRACT", ax, tie_half), 0.0, 0.002, rising=False)),
                  step(z, 1.535, 0.004, rising=False))
    pocket = math("MULTIPLY", math("MULTIPLY", front, math("MULTIPLY", step(x, 0.13, 0.003), step(x, 0.19, 0.003, rising=False))),
                  math("MULTIPLY", step(z, 1.36, 0.003), step(z, 1.385, 0.003, rising=False)))
    collar = math("MULTIPLY", step(z, COLLAR + 0.006, 0.004), step(ax, 0.075, 0.008, rising=False))
    cuff = step(ax, CUFF + 0.004, 0.003)
    white = math("MAXIMUM", math("MAXIMUM", in_v, pocket), math("MAXIMUM", collar, cuff))
    white = math("SUBTRACT", white, in_tie, clamp=True)
    shoes = attr("shoes")
    # jacket edges, darker: the hem all round, the button, the fronts opening below it
    hem = math("MULTIPLY", step(z, HEM - 0.007, 0.003), step(z, HEM + 0.004, 0.003, rising=False))
    hem = math("MULTIPLY", hem, step(ax, 0.30, 0.01, rising=False))  # not on the hands
    below = math("MULTIPLY", step(z, HEM, 0.003), step(z, 1.16, 0.003, rising=False))
    fronts = math("MULTIPLY", math("MULTIPLY", front, below),
                  step(math("ABSOLUTE", math("SUBTRACT", ax, math("MULTIPLY", math("SUBTRACT", 1.16, z), 0.35))),
                       0.004, 0.002, rising=False))
    button = math("MULTIPLY", front, step(math("ADD", math("MULTIPLY", ax, ax),
                                                    math("MULTIPLY", math("SUBTRACT", z, 1.15), math("SUBTRACT", z, 1.15))),
                                          0.00008, 0.00003, rising=False))
    edges = math("MAXIMUM", math("MAXIMUM", hem, fronts), button)

    def mix(a, b_, fac):
        n = N.new("ShaderNodeMix")
        n.data_type = "RGBA"
        L.new(fac, n.inputs["Factor"])
        for sock, val in (("A", a), ("B", b_)):
            if isinstance(val, tuple):
                n.inputs[sock].default_value = (*val, 1.0)
            else:
                L.new(val, n.inputs[sock])
        return n.outputs["Result"]

    col = mix(SUIT, tuple(c * 1.35 for c in SUIT), in_lapel)
    col = mix(col, SHIRT, white)
    col = mix(col, TIE, in_tie)
    col = mix(col, SHOES, shoes)
    col = mix(col, tuple(c * 0.3 for c in SUIT), edges)
    L.new(col, b.inputs["Base Color"])
    rough = math("ADD", 0.8, math("MULTIPLY", in_lapel, -0.35))
    rough = math("ADD", rough, math("MULTIPLY", in_tie, -0.4))
    rough = math("ADD", rough, math("MULTIPLY", shoes, -0.6), clamp=True)
    L.new(rough, b.inputs["Roughness"])
    # woven texture and soft folds on the cloth
    tex = node("ShaderNodeTexNoise", Scale=160.0, Detail=2.0)
    folds = node("ShaderNodeTexNoise", Scale=8.0, Detail=1.0)
    h = math("ADD", tex.outputs["Fac"], math("MULTIPLY", folds.outputs["Fac"], 2.0))
    bump = node("ShaderNodeBump", Strength=0.1, Distance=0.004)
    L.new(h, bump.inputs["Height"])
    L.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


def fix_character_materials(body, hair):
    tex = UBC + "/Hairstyles/Textures/"
    for img in bpy.data.images:
        # the eyebrow/eye materials point at normal maps that are named differently on disk
        if img.filepath.endswith("_Normal_png.png"):
            base = os.path.basename(img.filepath).replace("_png.png", ".png")
            for cand in (tex + base, UBC + "/Base Characters/Textures/" + base):
                if os.path.exists(cand):
                    img.filepath = cand
                    break
    # hair and eyebrows: tinted dark brown, a little sheen
    brows = bpy.data.objects.get("Eyebrows")
    mats = list(hair.data.materials) + (list(brows.data.materials) if brows else [])
    for m in mats:
        b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        img = next((n for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and "BaseColor" in n.image.name), None)
        if img is not None:
            if not os.path.exists(bpy.path.abspath(img.image.filepath)):
                img.image.filepath = tex + "T_Hair_1_BaseColor.png"
            mul = m.node_tree.nodes.new("ShaderNodeMix")
            mul.data_type = "RGBA"
            mul.blend_type = "MULTIPLY"
            mul.inputs["Factor"].default_value = 1.0
            mul.inputs["B"].default_value = (*[c * 1.5 for c in HAIR], 1.0)
            m.node_tree.links.new(img.outputs["Color"], mul.inputs["A"])
            m.node_tree.links.new(mul.outputs["Result"], b.inputs["Base Color"])
        else:
            b.inputs["Base Color"].default_value = (*HAIR, 1.0)
        b.inputs["Roughness"].default_value = 0.45


# ---- chair ------------------------------------------------------------------------------------


def load_chair():
    objs = imported(lambda: bpy.ops.import_scene.gltf(filepath=CHAIR))
    chair = next(o for o in objs if o.type == "MESH")
    chair.scale = [CHAIR_SCALE * k for k in CHAIR_SHAPE]
    for m in chair.data.materials:
        if "pillow" in m.name and "modern_arm_chair" in CHAIR:  # that chair's leather: burgundy
            nt = m.node_tree
            b = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
            link = next((l for l in nt.links if l.to_socket == b.inputs["Base Color"]), None)
            if link:
                # keep the leather's grain and wear (its brightness), recolour it burgundy
                bw = nt.nodes.new("ShaderNodeRGBToBW")
                nt.links.new(link.from_socket, bw.inputs["Color"])
                mul = nt.nodes.new("ShaderNodeMix")
                mul.data_type = "RGBA"
                mul.blend_type = "MULTIPLY"
                mul.inputs["Factor"].default_value = 1.0
                mul.inputs["B"].default_value = (*LEATHER_TINT, 1.0)
                nt.links.new(bw.outputs["Val"], mul.inputs["A"])
                nt.links.new(mul.outputs["Result"], b.inputs["Base Color"])
    return chair


def soften_chair(chair, body):
    """The leather gives: every chair point that his posed body (the suit) would go into is moved
    out to the body's surface, along the body's normal, so he sinks into the cushion instead of
    through it. Subdivided first, so the dent is smooth. Points he doesn't touch don't move."""
    sub = chair.modifiers.new("fine", "SUBSURF")
    sub.levels = sub.render_levels = 2
    sub.subdivision_type = "SIMPLE"
    ng = bpy.data.node_groups.new("cushion", "GeometryNodeTree")
    ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = ng.nodes
    gin, gout = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    info = n.new("GeometryNodeObjectInfo")
    info.transform_space = "RELATIVE"
    info.inputs["Object"].default_value = body
    near = n.new("GeometryNodeProximity")  # nearest point on his surface
    near.target_element = "FACES"
    normal = n.new("GeometryNodeSampleNearestSurface")  # ... and his surface normal there
    normal.data_type = "FLOAT_VECTOR"
    pos, body_normal = n.new("GeometryNodeInputPosition"), n.new("GeometryNodeInputNormal")
    away = n.new("ShaderNodeVectorMath")
    away.operation = "SUBTRACT"
    side = n.new("ShaderNodeVectorMath")
    side.operation = "DOT_PRODUCT"
    depth = n.new("ShaderNodeMath")  # how far inside him (0 outside)
    depth.operation = "MULTIPLY_ADD"
    depth.inputs[1].default_value = -1.0
    depth.inputs[2].default_value = CUSHION_MARGIN
    depth.use_clamp = False
    inside = n.new("ShaderNodeMath")
    inside.operation = "LESS_THAN"
    inside.inputs[1].default_value = 0.0
    close = n.new("ShaderNodeMath")
    close.operation = "LESS_THAN"
    close.inputs[1].default_value = CUSHION_GIVE
    both = n.new("ShaderNodeMath")
    both.operation = "MULTIPLY"
    amount = n.new("ShaderNodeMath")
    amount.operation = "MULTIPLY"
    push = n.new("ShaderNodeVectorMath")
    push.operation = "SCALE"
    setp = n.new("GeometryNodeSetPosition")
    L = ng.links.new
    L(gin.outputs[0], setp.inputs["Geometry"])
    L(info.outputs["Geometry"], near.inputs[0])
    L(info.outputs["Geometry"], normal.inputs["Mesh"])
    L(body_normal.outputs[0], normal.inputs["Value"])
    L(pos.outputs[0], away.inputs[0])
    L(near.outputs["Position"], away.inputs[1])
    L(away.outputs[0], side.inputs[0])
    L(normal.outputs["Value"], side.inputs[1])
    L(side.outputs["Value"], depth.inputs[0])  # margin - signed distance
    L(side.outputs["Value"], inside.inputs[0])
    L(near.outputs["Distance"], close.inputs[0])
    L(inside.outputs[0], both.inputs[0])
    L(close.outputs[0], both.inputs[1])
    L(depth.outputs[0], amount.inputs[0])
    L(both.outputs[0], amount.inputs[1])
    L(normal.outputs["Value"], push.inputs[0])
    L(amount.outputs[0], push.inputs["Scale"])
    L(push.outputs[0], setp.inputs["Offset"])
    L(setp.outputs[0], gout.inputs[0])
    mod = chair.modifiers.new("cushion", "NODES")
    mod.node_group = ng


# ---- animation ----------------------------------------------------------------------------------


class Clips:
    """Samples the library's actions on our rig (same bone names) without assigning them."""

    def __init__(self):
        objs = imported(lambda: bpy.ops.import_scene.gltf(filepath=UAL))
        for o in objs:
            bpy.data.objects.remove(o)
        self.actions = {a.name: a for a in bpy.data.actions}

    def channels(self, act):
        out = {}
        for fc in act_fcurves(act):
            path = fc.data_path
            if not path.startswith('pose.bones["'):
                continue
            bone = path.split('"')[1]
            prop = path.rsplit(".", 1)[1]
            out.setdefault((bone, prop), {})[fc.array_index] = fc
        return out

    def pose(self, name, t_clip):
        """Bone -> (location, rotation quaternion) at clip frame t_clip (clamped)."""
        act = self.actions[name]
        f0, f1 = act.frame_range
        t = min(max(t_clip, f0), f1)
        res = {}
        for (bone, prop), fcs in self.channels(act).items():
            if prop == "rotation_quaternion":
                q = Quaternion([fcs[i].evaluate(t) if i in fcs else (1, 0, 0, 0)[i] for i in range(4)])
                res.setdefault(bone, [Vector(), Quaternion()])[1] = q.normalized()
            elif prop == "location":
                loc = Vector([fcs[i].evaluate(t) if i in fcs else 0.0 for i in range(3)])
                res.setdefault(bone, [Vector(), Quaternion()])[0] = loc
        return res


def act_fcurves(act):
    """F-curves of an action (Blender 4.4+ keeps them in layered channelbags)."""
    if hasattr(act, "layers") and act.layers:
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves
    else:
        yield from act.fcurves


def to_scene(p):
    """Old-scene point -> this scene."""
    q = Vector(p) - OLD_CHAIR
    return Vector((-q.z, -q.x, q.y))


def frame_to_scene(cols):
    """Old-scene frame (columns forward, up, right) -> a rotation taking the rig's rest frame
    (forward -Y, up +Z, right -X) to it."""
    fwd, up, right = (to_scene(Vector(c) + OLD_CHAIR) for c in cols)
    posed = Matrix((fwd, up, right)).transposed()
    rest = Matrix(((0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (-1.0, 0.0, 0.0))).transposed()
    return posed @ rest.transposed()


# ---- the laptop ---------------------------------------------------------------------------------


def load_laptop():
    """The laptop under one root: base (with its little stand) and the lid on a hinge at the back.
    Root axes: x across the keyboard, y from the front edge back to the hinge, z up; origin at the
    bottom of the base."""
    objs = imported(lambda: bpy.ops.import_scene.gltf(filepath=LAPTOP))
    root = bpy.data.objects.new("laptop", None)
    bpy.context.collection.objects.link(root)
    hinge = bpy.data.objects.new("laptop_hinge", None)
    bpy.context.collection.objects.link(hinge)
    hinge.parent = root
    hinge.location = Vector((0.0, 0.2, 0.08))  # model units (scaled with the root)
    for o in objs:
        if o.type != "MESH":
            continue
        if o.name.startswith("classic_laptop_screen"):
            o.parent = hinge
            o.location = o.location - hinge.location
            for m in o.data.materials:
                if m.name == "classic_laptop_screen":
                    screen_material(m)
        else:
            o.parent = root
    root.scale = (LAPTOP_SCALE,) * 3
    return root, hinge


def screen_material(m):
    """The display, switched off: matte black. `light_screen()` puts the splash on it."""
    nt = m.node_tree
    b = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    for link in [l for l in nt.links if l.to_node == b and l.to_socket.name != "Normal"]:
        nt.links.remove(link)  # the model's own screen texture
    b.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    b.inputs["Emission Strength"].default_value = 0.0
    b.inputs["Roughness"].default_value = 0.6
    b.inputs["Specular IOR Level"].default_value = 0.02  # no room reflections: pure black when it fills the frame
    return b


def tone_inverse():
    """Inverse of the film's tone mapping (AgX, its look and exposure) for emissive surfaces, as
    a lookup table: display RGB (33^3 grid, 0..1) -> the linear emission that renders as it.
    Measured: a 17^3 grid of emission colours rendered flat through the same view settings."""
    import numpy as np

    if os.path.exists(TONE_LUT):
        return np.load(TONE_LUT)
    main = bpy.context.window.scene if bpy.context.window else bpy.context.scene
    n = 17
    levels = np.concatenate([[0.0], 2.0 ** np.linspace(-9, 4, n - 1)])  # linear emission
    grid = np.stack(np.meshgrid(levels, levels, levels, indexing="ij"), -1).reshape(-1, 3)
    w, h = n * n, n
    sc = bpy.data.scenes.new("tone_cal")
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 4
    sc.cycles.use_denoising = False
    sc.cycles.pixel_filter_type = "BOX"
    sc.cycles.filter_width = 0.01
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_depth = "8"
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    sc.view_settings.exposure = -0.35
    sc.world = bpy.data.worlds.new("tone_cal")
    sc.world.color = (0, 0, 0)
    img = bpy.data.images.new("tone_cal", w, h, float_buffer=True)
    img.colorspace_settings.name = "Linear Rec.709"
    px = np.ones((h, w, 4), np.float32)
    px[..., :3] = grid.reshape(n, n, n, 3).transpose(2, 0, 1, 3).reshape(n, n * n, 3)  # row = b
    img.pixels.foreach_set(px.ravel())
    mat = bpy.data.materials.new("tone_cal")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    tex, emi, out = nt.nodes.new("ShaderNodeTexImage"), nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeOutputMaterial")
    tex.image, tex.interpolation = img, "Closest"
    nt.links.new(tex.outputs["Color"], emi.inputs["Color"])
    nt.links.new(emi.outputs[0], out.inputs["Surface"])
    bpy.ops.mesh.primitive_plane_add()
    plane = bpy.context.object
    for c in plane.users_collection:
        c.objects.unlink(plane)
    sc.collection.objects.link(plane)
    plane.scale = (w / h, 1, 1)
    plane.data.materials.append(mat)
    cam = bpy.data.objects.new("tone_cal", bpy.data.cameras.new("tone_cal"))
    cam.data.type, cam.data.ortho_scale = "ORTHO", 2.0 * w / h
    cam.location = (0, 0, 1)
    sc.collection.objects.link(cam)
    sc.camera = cam
    path = os.path.join(os.path.dirname(TONE_LUT), "tone_cal.png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True, scene=sc.name)
    shot = bpy.data.images.load(path)
    shot.colorspace_settings.name = "Non-Color"  # the display values as stored
    # rows bottom-up like the input: row = blue level, columns = red * n + green
    shown = np.array(shot.pixels[:], np.float32).reshape(h, w, 4)[:, :, :3]
    shown = shown.reshape(n, n, n, 3).transpose(1, 2, 0, 3).reshape(-1, 3)  # same order as grid
    for o in (plane, cam):
        bpy.data.objects.remove(o)
    bpy.data.scenes.remove(sc)
    if bpy.context.window is not None:
        bpy.context.window.scene = main
    # invert: for each display colour on a 33^3 grid, the emission whose display is nearest
    # (in the measured cloud, refined by inverse-distance weighting of the 4 nearest)
    m = 33
    t = np.linspace(0, 1, m)
    want = np.stack(np.meshgrid(t, t, t, indexing="ij"), -1).reshape(-1, 3)
    lut = np.zeros_like(want)
    for i in range(0, len(want), 2048):
        d = ((want[i:i + 2048, None, :] - shown[None, :, :]) ** 2).sum(-1)
        idx = np.argpartition(d, 4, axis=1)[:, :4]
        dd = np.take_along_axis(d, idx, 1)
        wgt = 1.0 / (dd + 1e-6)
        lut[i:i + 2048] = (grid[idx] * wgt[..., None]).sum(1) / wgt.sum(1, keepdims=True)
    lut = lut.reshape(m, m, m, 3)
    np.save(TONE_LUT, lut)
    return lut


def apply_lut(lut, rgb):
    """Trilinear lookup of display RGB (N x 3, 0..1) in `lut`."""
    import numpy as np

    m = lut.shape[0]
    p = np.clip(rgb, 0, 1) * (m - 1)
    i0 = np.minimum(np.floor(p).astype(int), m - 2)
    f = p - i0
    out = np.zeros_like(rgb)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                wgt = (f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1]) * (f[:, 2] if dz else 1 - f[:, 2])
                out += lut[i0[:, 0] + dx, i0[:, 1] + dy, i0[:, 2] + dz] * wgt[:, None]
    return out


def light_screen():
    """The screen shows the brand splash (`src/intro/splash.rs`), drawn by the player itself as a
    texture, so that the film's last frames show what the player then draws crisp in the terminal:
    the texture is laid out so that the part the final camera sees is the splash's picture."""
    sc = bpy.context.scene
    sc.frame_set(sc.frame_end)  # the final camera
    scr = bpy.data.objects["classic_laptop_screen"]
    slot = next(i for i, m in enumerate(scr.material_slots) if m.material.name == "classic_laptop_screen")
    disp = [scr.data.vertices[i].co for p in scr.data.polygons if p.material_index == slot for i in p.vertices]
    x0, x1 = min(v.x for v in disp), max(v.x for v in disp)
    z0, z1 = min(v.z for v in disp), max(v.z for v in disp)
    plane_y = sum(v.y for v in disp) / len(disp)
    # where the final frame sits on the display: project the display's corners into the camera
    # (0..1 across the frame, y up) and extend linearly (the camera faces the screen squarely)
    from bpy_extras.object_utils import world_to_camera_view

    cam = sc.camera
    corner = lambda x, z: world_to_camera_view(sc, cam, scr.matrix_world @ Vector((x, plane_y, z)))
    left, right = corner(x0, (z0 + z1) / 2), corner(x1, (z0 + z1) / 2)
    bottom, top = corner((x0 + x1) / 2, z0), corner((x0 + x1) / 2, z1)
    flip_u = left.x > right.x  # the display's +x runs right to left as seen
    if flip_u:
        left, right = right, left
    u = lambda fx: (fx - left.x) / (right.x - left.x)  # frame x -> display u (0 = left edge)
    v = lambda fy: (top.y - fy) / (top.y - bottom.y)  # frame y (up) -> display v (0 = top edge)
    frame = (u(0.0), v(1.0), u(1.0), v(0.0))
    os.makedirs(os.path.dirname(SPLASH_TEXTURE), exist_ok=True)
    w = 1464
    h = round(w * (z1 - z0) / (x1 - x0))
    res = (sc.render.resolution_x, sc.render.resolution_y)
    subprocess.run([PLAYER, "--splash-texture", SPLASH_TEXTURE, "--texture-size", f"{w}x{h}",
                    "--film-aspect", f"{res[0] / res[1]:.6f}",
                    "--frame", ",".join(f"{f:.5f}" for f in frame)], check=True)
    print(f"splash texture {w}x{h}, final frame on the display {tuple(round(f, 4) for f in frame)}")
    # display coordinates from the object's own: u across, v up (an image's v)
    m = scr.material_slots[slot].material
    nt = m.node_tree
    b = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    coord, sep, comb = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeSeparateXYZ"), nt.nodes.new("ShaderNodeCombineXYZ")
    mu, mv = nt.nodes.new("ShaderNodeMapRange"), nt.nodes.new("ShaderNodeMapRange")
    mu.inputs["From Min"].default_value, mu.inputs["From Max"].default_value = (x1, x0) if flip_u else (x0, x1)
    mv.inputs["From Min"].default_value, mv.inputs["From Max"].default_value = z0, z1
    # the texture as linear emission that the film's tone mapping turns back into its colours
    import numpy as np

    shown = bpy.data.images.load(SPLASH_TEXTURE)
    shown.colorspace_settings.name = "Non-Color"  # raw display values
    disp_px = np.array(shown.pixels[:], np.float32).reshape(-1, 4)
    emission = apply_lut(tone_inverse(), disp_px[:, :3])
    lit = bpy.data.images.new("splash_emission", shown.size[0], shown.size[1], float_buffer=True)
    lit.colorspace_settings.name = "Linear Rec.709"
    lit.pixels.foreach_set(np.concatenate([emission, np.ones((len(emission), 1), np.float32)], 1).astype(np.float32).ravel())
    lit.pack()
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = lit
    tex.extension = "EXTEND"
    nt.links.new(coord.outputs["Object"], sep.inputs[0])
    nt.links.new(sep.outputs["X"], mu.inputs["Value"])
    nt.links.new(sep.outputs["Z"], mv.inputs["Value"])
    nt.links.new(mu.outputs["Result"], comb.inputs["X"])
    nt.links.new(mv.outputs["Result"], comb.inputs["Y"])
    nt.links.new(comb.outputs[0], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], b.inputs["Emission Color"])
    # it lights up as the lid opens
    s0, s1 = LAPTOP_BEATS["screen_on"]
    for f in range(sc.frame_start, sc.frame_end + 1):
        b.inputs["Emission Strength"].default_value = SCREEN_GLOW * smoothstep(s0, s1, f / FPS)
        b.inputs["Emission Strength"].keyframe_insert("default_value", frame=f)


# laptop root axes in the hand, carried closed at his left side: across the keyboard pointing
# forward, front edge up (the hand holds it there), base facing outward
GRIP_LOCAL = Vector((0.0, -0.235, 0.06))  # model units: middle of the front edge, closed
SIDE_LOCAL = Vector((-0.39, -0.17, 0.05))  # model units: the left hand's grip outside its near-left corner, held flat
LID_EDGE_LOCAL = Vector((0.2, 0.03, 0.46))  # model units in the hinge frame: the lid's top edge, right of centre (closed: on top)
LID_EDGE_OPEN_LOCAL = Vector((0.2, -0.15, 0.52))  # ... as it opens the fingers come round to its front (the display side, -y)
PALM_LOCAL = {"l": Vector((-0.2, -0.17, 0.11)), "r": Vector((0.2, -0.17, 0.11))}
CARRY_DROP = 0.06  # metres: the front edge sits this far below his wrist, in his fingers
HAND_SOFT = 1.0  # how much of the library's relaxed seated hand the hands on the keyboard take
FINGER_CURL = 0.35  # ... with the fingers this far toward a grip: curved down onto the keys
PALM_LIFT = 0.045  # metres: the wrist rests this far above the palm rest (the hand's own thickness)
LID_CLOSED = 94.0  # degrees the lid turns about the hinge to lie on the keyboard
LID_TILT_MAX = 30.0  # opened at most this far past upright; aimed at his eyes (computed)
THIGH_GROUPS = ("thigh",)  # suit vertices the laptop rests on


def animate_laptop(arm, frames, laptop, hinge, clips):
    """Carry, bring round in front, put on the lap, open; the arms' IK targets follow (overriding
    the classic arms). On his lap the laptop rests on top of the posed suit's thighs."""
    sc = bpy.context.scene
    s = LAPTOP_SCALE
    tl, tr = (bpy.data.objects[f"arm_{x}_target"] for x in "lr")
    pl, pr = (bpy.data.objects[f"arm_{x}_pole"] for x in "lr")
    suit = bpy.data.objects["Suit"]
    rests_on = [v.index for v in suit.data.vertices
                if v.groups and suit.vertex_groups[max(v.groups, key=lambda g: g.weight).group].name.startswith(THIGH_GROUPS)]
    base = [o.matrix_local @ v.co for o in laptop.children if o.type == "MESH" for v in o.data.vertices]
    x0, x1 = min(v.x for v in base), max(v.x for v in base)
    y0, y1 = min(v.y for v in base), max(v.y for v in base)

    def body(fr):
        f = frame_to_scene(fr["body"])
        # (frame_to_scene maps the rig's rest frame; recover the posed axes)
        rest = Matrix(((0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (-1.0, 0.0, 0.0))).transposed()
        posed = f @ rest
        return posed.col[0], posed.col[1], posed.col[2]

    def hips(fr):
        h = (to_scene(fr["hip"][0]) + to_scene(fr["hip"][1])) * 0.5
        return h + Vector((0.0, 0.0, SEAT_LIFT * smoothstep(0.78, 0.6, fr["pelvis"][1])))

    def carry(fr):
        fwd, up, right = body(fr)
        left = -right
        shoulder = to_scene(fr["shoulder"][0])
        hang = shoulder - up * 0.56 + fwd * 0.04 + left * 0.1
        old = to_scene(fr["hand"][0])
        wrist = old.lerp(hang, 0.7)  # the laptop's weight damps the swing
        rot = Matrix((fwd, -up, left)).transposed()  # root x, y, z
        root = wrist - up * CARRY_DROP - rot @ (GRIP_LOCAL * s)
        return Matrix.Translation(root) @ rot.to_4x4()

    def front(fr):
        # held flat in front of his hips, front edge toward him
        fwd, up, right = body(fr)
        flat_fwd = Vector((fwd.x, fwd.y, 0.0)).normalized()
        x = Vector((-flat_fwd.y, flat_fwd.x, 0.0))  # his right
        if x.dot(right) < 0:
            x = -x
        rot = Matrix((x, flat_fwd, Vector((0.0, 0.0, 1.0)))).transposed()
        # in his left hand by its near-left corner, the elbow bent square at his side; it reaches
        # across in front of him, the forearm outside it
        wrist = to_scene(fr["shoulder"][0]) - up * 0.27 + flat_fwd * 0.26 + right * 0.05
        return Matrix.Translation(wrist - rot @ (SIDE_LOCAL * s)) @ rot.to_4x4()

    def lap(fr):
        hip = hips(fr)
        knees = [to_scene(fr["knee"][i]) for i in (0, 1)]
        mid_knee = (knees[0] + knees[1]) * 0.5
        along = (mid_knee - hip).normalized()  # down the thighs, away from him
        x = Vector((-1.0, 0.0, 0.0))  # across the keyboard: his right
        z = along.cross(x).normalized()
        if z.z < 0:
            z = -z
        y = z.cross(x).normalized()
        if y.dot(along) < 0:
            y = -y
        rot = Matrix((x, y, z)).transposed()
        return Matrix.Translation(hip.lerp(mid_knee, 0.64)) @ rot.to_4x4()  # clear of his belly

    def blend(a, b, u, arc=Vector()):
        a, b = a.decompose(), b.decompose()
        return Matrix.LocRotScale(a[0].lerp(b[0], u) + arc * math.sin(math.pi * u), a[1].slerp(b[1], u), Vector((1, 1, 1)))

    def lift_off(m, verts, below):
        """How far m must rise along its own up axis so that none of these suit vertices is inside
        the base (vertices under its footprint and lower than `below`, model units)."""
        dg = bpy.context.evaluated_depsgraph_get()
        ev = suit.evaluated_get(dg)
        me = ev.to_mesh()
        inv = (m @ Matrix.Diagonal((s, s, s, 1.0))).inverted() @ ev.matrix_world
        top = 0.0
        for i in verts:
            p = inv @ me.vertices[i].co
            if x0 <= p.x <= x1 and y0 <= p.y <= y1 and p.z < below:
                top = max(top, p.z)
        ev.to_mesh_clear()
        return (top + 0.004) * s if top > 0 else 0.0

    def raised(m, lift):
        return Matrix.Translation(m.col[2].to_3d().normalized() * lift) @ m

    def resting(m):
        """m raised along its own up axis until the base sits on the thighs (not in them)."""
        return raised(m, lift_off(m, rests_on, 0.3))

    # the lid opens until the screen faces his eyes (at the end), at most LID_TILT_MAX past upright
    sc.frame_set(len(frames) - 1)
    end = resting(lap(frames[-1]))
    eyes = bpy.data.objects["Eyes"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    eye = sum((eyes.matrix_world @ v.co for v in eyes.data.vertices), Vector()) / len(eyes.data.vertices)
    scr = next(o for o in hinge.children if o.type == "MESH")
    disp = [scr.matrix_local @ scr.data.vertices[i].co for p in scr.data.polygons
            if scr.material_slots[p.material_index].material.name == "classic_laptop_screen" for i in p.vertices]
    disp_c = sum(disp, Vector()) / len(disp)  # in the hinge frame (closed = rotation LID_CLOSED)

    def facing(angle):
        hm = end @ Matrix.Diagonal((s, s, s, 1.0)) @ Matrix.Translation(hinge.location) @ Matrix.Rotation(math.radians(angle), 4, "X")
        c = hm @ disp_c
        n = (hm.to_3x3() @ Vector((0.0, -1.0, 0.0))).normalized()
        return n.dot((eye - c).normalized())

    lid_open = max(range(-int(LID_TILT_MAX), 31), key=facing)
    print(f"lid opens to {lid_open} deg (screen facing his eyes: {facing(lid_open):.3f})")

    c0, c1 = LAPTOP_BEATS["to_front"]
    l0, l1 = LAPTOP_BEATS["to_lap"]
    h0, h1 = LAPTOP_BEATS["rest_hand"]
    # how far each hand has settled on the keyboard (0 .. 1)
    rest_l = lambda t: smoothstep(l1 + 0.3, l1 + 0.8, t)
    rest_r = lambda t: smoothstep(h0 + 0.1, h1 + 0.1, t)
    # the library's seated hands (resting on the thighs): wrist and fingers soft, blended in as
    # each hand settles on the keyboard
    soft = clips.pose("Sitting_Idle_Loop", 12.0)
    grip = clips.pose("Driving_Loop", 12.0)  # fingers round a steering wheel: for the curl
    for name, (loc, q) in soft.items():
        if name.startswith(("index", "middle", "ring", "pinky")) and name in grip:
            soft[name] = [loc, q.slerp(grip[name][1], FINGER_CURL)]

    def left_hand(t, lm, up, right):
        """Where the left wrist goes. It stays on the laptop: the front edge while carrying, the
        near-left corner while held in front, then the palm rest."""
        lap_up = lm.to_3x3().col[2].normalized()
        if t <= c0:
            return lm @ GRIP_LOCAL + up * CARRY_DROP
        if t <= c1:
            # from the edge it is carried by to its near-left corner, round the outside
            u = smoothstep(c0, c1, t)
            return lm @ GRIP_LOCAL.lerp(SIDE_LOCAL, u) + up * CARRY_DROP * (1.0 - u) \
                + (-right * 0.08) * math.sin(math.pi * u)
        if t <= l1 + 0.1:
            return lm @ SIDE_LOCAL  # it holds it until it is on his thighs
        # then over the top onto the palm rest
        v = smoothstep(l1 + 0.1, l1 + 0.7, t)
        p = SIDE_LOCAL.lerp(PALM_LOCAL["l"], v) + Vector((0.0, 0.0, 0.22)) * math.sin(math.pi * v)
        return lm @ p + lap_up * PALM_LIFT * v

    for f, fr in enumerate(frames):
        t = f / FPS
        fwd, up, right = body(fr)
        sc.frame_set(f)
        # ---- the left elbow: by his side while it is held in front, out once it is on his lap
        shoulder_l = to_scene(fr["shoulder"][0])
        if t <= c0:
            pole = shoulder_l - up * 0.3 - fwd * 0.3 + (-right) * 0.15
        elif t <= l0:
            pole = shoulder_l - up * 0.5 - fwd * 0.1 + (-right) * 0.3  # elbow down, out at his side
        else:
            pole = shoulder_l - up * 0.3 - fwd * 0.05 + (-right) * 0.4
            # resting on the keyboard: the elbow sinks toward his side
            pole = pole.lerp(shoulder_l - up * 0.6 - fwd * 0.15 + (-right) * 0.22, rest_l(t))
        if c0 < t <= c1:
            pole = (shoulder_l - up * 0.3 - fwd * 0.3 + (-right) * 0.15).lerp(pole, smoothstep(c0, c1, t))
        elif l0 < t <= l1:
            pole = (shoulder_l - up * 0.5 - fwd * 0.1 + (-right) * 0.3).lerp(pole, smoothstep(l0, l1, t))
        pl.location = pole
        pl.keyframe_insert("location", frame=f)
        # ---- where the laptop is
        if t <= c0:
            m = carry(fr)
        elif t <= c1:
            # up and round to the front, clear of his thigh and the chair
            m = blend(carry(fr), front(fr), smoothstep(c0, c1, t), fwd * 0.1 + up * 0.06)
        elif t <= l0:
            m = front(fr)
        else:
            m = blend(front(fr), lap(fr), smoothstep(l0, l1, t))
        if t > c0:
            m = resting(m)
        laptop.matrix_world = m @ Matrix.Diagonal((s, s, s, 1.0))
        laptop.keyframe_insert("location", frame=f)
        laptop.keyframe_insert("rotation_euler", frame=f)
        o0, o1 = LAPTOP_BEATS["open"]
        k = smoothstep(o0, o1, t)
        hinge.rotation_euler = (math.radians(LID_CLOSED + (lid_open - LID_CLOSED) * k), 0.0, 0.0)
        hinge.keyframe_insert("rotation_euler", frame=f)
        sc.frame_set(f)  # (the hinge and root as keyed, for the points below)
        lm = laptop.matrix_world
        hm = hinge.matrix_world
        lap_up = lm.to_3x3().col[2].normalized()
        tl.location = left_hand(t, laptop.matrix_world, up, right)
        # ---- right hand: classic (armrest) until it reaches for the lid, follows the edge, rests
        r0, r1 = LAPTOP_BEATS["reach_lid"]
        old_r = tr.location.copy()  # as keyed by animate_classic for this frame
        edge = hm @ LID_EDGE_LOCAL.lerp(LID_EDGE_OPEN_LOCAL, k) + up * 0.02
        palm_r = lm @ PALM_LOCAL["r"] + lap_up * PALM_LIFT
        h0, h1 = LAPTOP_BEATS["rest_hand"]
        if t < r0:
            target = old_r
        elif t < r1:
            target = old_r.lerp(edge, smoothstep(r0, r1, t))
        elif t < h0:
            target = edge
        else:
            target = edge.lerp(palm_r, smoothstep(h0, h1, t))
        tr.location = target
        if t >= r0:
            shoulder_r = to_scene(fr["shoulder"][1])
            k = smoothstep(r0, r1, t)
            reach = shoulder_r - up * 0.3 - fwd * 0.05 + right * 0.4
            reach = reach.lerp(shoulder_r - up * 0.6 - fwd * 0.15 + right * 0.22, rest_r(t))
            pr.location = pr.location.lerp(reach, k)
        for o in (tl, tr, pl, pr):
            o.keyframe_insert("location", frame=f)
        for side, w in (("l", rest_l(t)), ("r", rest_r(t))):
            if w <= 0.0:
                continue
            for name, (_, q) in soft.items():
                if name.endswith("_" + side) and (name.startswith(FINGERS) or name == "hand_" + side):
                    pb = arm.pose.bones[name]
                    pb.rotation_quaternion = pb.rotation_quaternion.slerp(q, w * HAND_SOFT)
                    pb.keyframe_insert("rotation_quaternion", frame=f)


def load_motion():
    import json

    with open(MOTION_FILE) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def animate_classic(arm, clips):
    """Bakes the classic motion onto the rig. The torso follows the old trunk, chest and head
    frames; legs and arms reach the old ankles and wrists by IK, bending toward the old knees and
    elbows; feet aim at the old toes. Fingers rest in the library's relaxed hand."""
    sc = bpy.context.scene
    frames = load_motion()
    n = len(frames) - 1
    bones = arm.data.bones
    rest = {b.name: b.matrix_local.to_3x3() for b in bones}
    pelvis_head = bones["pelvis"].head_local.copy()
    hip_mid = (bones["thigh_l"].head_local + bones["thigh_r"].head_local) * 0.5
    relaxed = clips.pose("Walk_Formal_Loop", 0.0)
    fingers = {b: r for b, (l, r) in relaxed.items() if b.startswith(FINGERS)}

    def empty(name):
        e = bpy.data.objects.new(name, None)
        sc.collection.objects.link(e)
        return e

    limbs = {}
    for side, idx in (("l", 0), ("r", 1)):
        for chain, end, kind in ((f"calf_{side}", "ankle", "leg"), (f"lowerarm_{side}", "hand", "arm")):
            tgt, pole = empty(f"{kind}_{side}_target"), empty(f"{kind}_{side}_pole")
            c = arm.pose.bones[chain].constraints.new("IK")
            c.target, c.pole_target, c.chain_count = tgt, pole, 2
            c.pole_angle = math.radians(-90.0)
            limbs[(kind, side)] = (tgt, pole, idx)
        toe = empty(f"toe_{side}")
        aim = arm.pose.bones[f"foot_{side}"].constraints.new("DAMPED_TRACK")
        aim.target, aim.track_axis = toe, "TRACK_Y"
        limbs[("toe", side)] = (toe, None, idx)
        # the toes bend at the ball and stay along the floor when the heel rises
        flat = empty(f"toes_flat_{side}")
        toes = arm.pose.bones[f"ball_{side}"].constraints.new("DAMPED_TRACK")
        toes.target, toes.track_axis = flat, "TRACK_Y"
        limbs[("flat", side)] = (flat, None, idx)

    # torso bones and the frame each follows (blends: halfway between two frames)
    def frames_of(fr):
        trunk, chest, head = (frame_to_scene(fr[k]) for k in ("trunk", "chest", "head_frame"))
        half = lambda a, b: a.to_quaternion().slerp(b.to_quaternion(), 0.5).to_matrix()
        return {"pelvis": trunk, "spine_01": trunk, "spine_02": half(trunk, chest),
                "spine_03": chest, "neck_01": half(chest, head), "Head": head}

    arm.animation_data_create()
    arm.rotation_mode = "XYZ"
    arm.rotation_euler = (0.0, 0.0, 0.0)
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    for f, fr in enumerate(frames):
        world = frames_of(fr)
        # the hips sit where the old hips are; the pelvis turns about its own head
        old_hips = (to_scene(fr["hip"][0]) + to_scene(fr["hip"][1])) * 0.5
        # this body carries more below the hip joints than the old one: seated, lift it onto
        # the cushion (0 standing, full once the old pelvis is down at seat height)
        old_pelvis_h = fr["pelvis"][1]
        old_hips = old_hips + Vector((0.0, 0.0, SEAT_LIFT * smoothstep(0.78, 0.6, old_pelvis_h)))
        arm.location = old_hips - world["pelvis"] @ (hip_mid - pelvis_head) - pelvis_head
        arm.keyframe_insert("location", frame=f)
        for b in bones:
            p = b.parent
            while p is not None and p.name not in world:
                p = p.parent
            rp = world[p.name] if p is not None else Matrix.Identity(3)
            r = world.get(b.name, rp)
            q = (rest[b.name].inverted() @ rp.inverted() @ r @ rest[b.name]).to_quaternion()
            if b.name.startswith(FINGERS):
                q = fingers.get(b.name, q)
            pb = arm.pose.bones[b.name]
            pb.rotation_quaternion = q
            pb.keyframe_insert("rotation_quaternion", frame=f)
        for (kind, side), (tgt, pole, idx) in limbs.items():
            if kind == "leg":
                tgt.location = to_scene(fr["ankle"][idx]) + Vector((0.0, 0.0, ANKLE_LIFT))
                pole.location = to_scene(fr["knee"][idx]) * 2 - (to_scene(fr["hip"][idx]) + to_scene(fr["ankle"][idx])) * 0.5 \
                    + facing_of(world["pelvis"]) * 0.3
            elif kind == "arm":
                hand, elbow = to_scene(fr["hand"][idx]), to_scene(fr["elbow"][idx])
                wrist = hand - (hand - elbow).normalized() * 0.06
                tgt.location = wrist
                shoulder = to_scene(fr["shoulder"][idx])
                pole.location = elbow * 2 - (shoulder + wrist) * 0.5 - facing_of(world["pelvis"]) * 0.15
            elif kind == "flat":
                heel, toe = to_scene(fr["heel"][idx]), to_scene(fr["toe"][idx])
                fwd = toe - heel
                fwd.z = 0.0
                ball = to_scene(fr["toe"][idx]) - fwd.normalized() * 0.04
                tgt.location = Vector((ball.x, ball.y, max(ball.z - 0.03, 0.0) + 0.012)) + fwd.normalized() * 0.3
            else:
                # the foot bone runs from the ankle down to the ball (0.89 forward, 0.45 down at
                # rest); aim it that way in the old foot's own frame, so a flat old foot is flat here
                ankle = to_scene(fr["ankle"][idx]) + Vector((0.0, 0.0, ANKLE_LIFT))
                heel, toe = to_scene(fr["heel"][idx]), to_scene(fr["toe"][idx])
                fwd = (toe - heel).normalized()
                down = ((heel + toe) * 0.5 - to_scene(fr["ankle"][idx]))
                down = (down - fwd * down.dot(fwd)).normalized()
                tgt.location = ankle + (fwd * 0.89 + down * 0.45) * 0.16
            tgt.keyframe_insert("location", frame=f)
            if pole is not None:
                pole.keyframe_insert("location", frame=f)
    sc.frame_start, sc.frame_end = 0, n
    return frames


def facing_of(rot):
    """Forward vector of a torso rotation (the rig faces -Y at rest)."""
    return rot @ Vector((0.0, -1.0, 0.0))


def camera_from_motion(frames):
    """The classic camera path: position, look-at point and vertical field of view per frame."""
    sc = bpy.context.scene
    target = bpy.data.objects.new("cam_target", None)
    sc.collection.objects.link(target)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.sensor_fit = "HORIZONTAL"  # the width is composed; the height follows the frame
    cam_data.clip_start = 0.02  # it ends close to his face
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    # the takeover: one continuous move. From the classic front view the camera spirals round his
    # left side and behind him (radius, height and look-at point change together on one eased
    # curve), ends just above and beside his eyes, and the lens narrows on the way in until the
    # screen's width fills the frame.
    sc.frame_set(len(frames) - 1)
    dg = bpy.context.evaluated_depsgraph_get()
    lo = bpy.data.objects["classic_laptop_screen"].evaluated_get(dg)
    me = lo.to_mesh()
    slot = next(i for i, m in enumerate(lo.material_slots) if m.material.name == "classic_laptop_screen")
    disp = [lo.matrix_world @ me.vertices[v].co for p in me.polygons if p.material_index == slot for v in p.vertices]
    lo.to_mesh_clear()
    screen_c = sum(disp, Vector()) / len(disp)  # the display's middle
    screen_w = max(v.x for v in disp) - min(v.x for v in disp)
    eyes = bpy.data.objects["Eyes"].evaluated_get(dg)
    eye = sum((eyes.matrix_world @ v.co for v in eyes.data.vertices), Vector()) / len(eyes.data.vertices)
    end_pos = eye + Vector(CAMERA_END_OFFSET)
    dist = (screen_c - end_pos).length
    fill = math.degrees(2 * math.atan((screen_w / DESIGN_ASPECT) * 0.5 / dist)) * 0.96  # a little past its edges
    f0 = round(CAMERA_TAKEOVER * FPS)
    start_pos, start_tgt, start_fov = to_scene(frames[f0]["cam_pos"]), to_scene(frames[f0]["cam_target"]), frames[f0]["cam_fov"]

    def polar(p):
        d = p - screen_c
        return math.atan2(d.y, d.x), d.xy.length, p.z

    a0, r0, z0 = polar(start_pos)
    a1, r1, z1 = polar(end_pos)
    while a1 < a0:
        a1 += 2 * math.pi  # counter-clockwise from above: from the front round his left (+x) side

    for f, fr in enumerate(frames):
        t = f / FPS
        if t < CAMERA_TAKEOVER:
            pos, tgt, fov = to_scene(fr["cam_pos"]), to_scene(fr["cam_target"]), fr["cam_fov"]
        else:
            u = min(1.0, (t - CAMERA_TAKEOVER) / (SCREEN_FILL_AT - CAMERA_TAKEOVER))
            e = u * u * u * (u * (u * 6 - 15) + 10)  # one eased curve for the whole move
            a = a0 + (a1 - a0) * e
            r = r0 + (r1 - r0) * e
            z = z0 + (z1 - z0) * e + CAMERA_RISE * math.sin(math.pi * e)  # up over the chair's back
            pos = screen_c + Vector((math.cos(a) * r, math.sin(a) * r, 0.0))
            pos.z = z
            tgt = start_tgt.lerp(screen_c, smoothstep(0.0, 0.6, e))
            # the lens narrows through the move, mostly at the end (log scale)
            fov = math.exp(math.log(start_fov) + (math.log(fill) - math.log(start_fov)) * e ** 4)
        cam.location, target.location = pos, tgt
        # the vertical field of view at the design aspect -> the same horizontal one
        cam_data.angle = 2 * math.atan(math.tan(math.radians(fov) / 2) * DESIGN_ASPECT)
        cam.keyframe_insert("location", frame=f)
        target.keyframe_insert("location", frame=f)
        cam_data.keyframe_insert("lens", frame=f)
    return cam


def lights_and_world():
    sc = bpy.context.scene
    world = bpy.data.worlds.new("dark")
    sc.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.004, 0.0038, 0.0045, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0

    def area(name, loc, target, power, color, size):
        d = bpy.data.lights.new(name, "AREA")
        d.energy, d.color, d.size = power, color, size
        o = bpy.data.objects.new(name, d)
        sc.collection.objects.link(o)
        o.location = loc
        o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        return o

    # key: front-left above (warm-white), rim: behind the chair (warm), faint cool fill
    area("key", (-2.2, -2.6, 3.0), (0, -0.2, 0.8), 520, (1.0, 0.9, 0.8), 1.6)
    area("rim", (1.8, 2.4, 2.6), (0, 0, 1.0), 380, (1.0, 0.55, 0.35), 1.2)
    area("fill", (2.8, -2.0, 1.4), (0, 0, 0.9), 45, (0.7, 0.8, 1.0), 2.5)
    # floor: dark wood-ish, with a round rug under the chair
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
    floor = bpy.context.object
    m, b = principled("M_floor", (0.018, 0.014, 0.012), 0.6)
    floor.data.materials.append(m)
    bpy.ops.mesh.primitive_cylinder_add(radius=1.25, depth=0.008, location=(0, -0.35, 0.004))
    rug = bpy.context.object
    m, b = principled("M_rug", (0.022, 0.011, 0.009), 0.95, sheen=0.05)
    rug.data.materials.append(m)


def render_settings(opts):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = opts["samples"]
    sc.cycles.use_denoising = True
    sc.render.fps = FPS
    sc.render.resolution_x, sc.render.resolution_y = opts["res"]
    sc.render.resolution_percentage = 100
    sc.render.threads_mode = "FIXED"
    sc.render.threads = int(os.environ.get("FORCE_INTRO_THREADS", "48"))
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    sc.view_settings.exposure = -0.35
    sc.cycles.max_bounces = 6


def main():
    opts = args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm, body, hair = load_character()
    slim_body(arm, body)
    fix_character_materials(body, hair)
    make_suit(arm, body)
    chair = load_chair()
    clips = Clips()
    frames = animate_classic(arm, clips)
    soften_chair(chair, bpy.data.objects["Suit"])
    laptop, hinge = load_laptop()
    animate_laptop(arm, frames, laptop, hinge, clips)
    render_settings(opts)  # (the frame's aspect, which light_screen() needs)
    camera_from_motion(frames)
    light_screen()
    lights_and_world()
    if opts["save"]:
        bpy.ops.wm.save_as_mainfile(filepath=opts["save"])
    if opts["out"]:
        sc = bpy.context.scene
        f0, f1 = opts["frames"] or (sc.frame_start, sc.frame_end)
        if opts["jobs"] > 1:
            render_parallel(opts, list(range(f0, f1 + 1)))
            return
        for f in range(f0, f1 + 1):
            sc.frame_set(f)
            sc.render.filepath = os.path.join(opts["out"], f"{f:04d}.png")
            bpy.ops.render.render(write_still=True)


def render_parallel(opts, frames):
    """Renders `frames` in `--jobs` Blender processes from the scene saved once: a frame this small
    spends much of its time on per-frame setup (scene update, BVH, denoiser), which one process
    can't spread over many threads. The threads (FORCE_INTRO_THREADS, default all) are shared out;
    frames are interleaved so every job gets its share of the heavier ones. Output is identical
    to rendering in this process."""
    out, jobs = opts["out"], min(opts["jobs"], len(frames))
    os.makedirs(out, exist_ok=True)
    blend = os.path.join(out, "scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    threads = int(os.environ.get("FORCE_INTRO_THREADS", str(os.cpu_count() or 48)))
    per_job = max(1, threads // jobs)
    procs = []
    for j in range(jobs):
        mine = frames[j::jobs]
        cmd = [bpy.app.binary_path, "-b", blend, "-t", str(per_job), "-o", os.path.join(out, "####"),
               "-F", "PNG", "-f", ",".join(map(str, mine))]
        log = open(os.path.join(out, f"job{j}.log"), "w")
        procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log, len(mine)))
    print(f"rendering {len(frames)} frames in {jobs} jobs x {per_job} threads", flush=True)
    failed = []
    for j, (p, log, n) in enumerate(procs):
        p.wait()
        log.close()
        if p.returncode != 0:
            failed.append(j)
    missing = [f for f in frames if not os.path.exists(os.path.join(out, f"{f:04d}.png"))]
    if failed or missing:
        raise SystemExit(f"render jobs {failed} failed; missing frames {missing[:10]} (see {out}/job*.log)")
    for j in range(jobs):
        os.remove(os.path.join(out, f"job{j}.log"))
    os.remove(blend)
    print(f"rendered {len(frames)} frames into {out}", flush=True)


main()
