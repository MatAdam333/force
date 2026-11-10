//! Scene primitives: signed distance functions, the armchair, and the man's body built from the
//! solved skeleton. World: metres, +Y up, floor at y = 0. The chair faces +X; in the side view
//! the camera looks along -Z, so +X is screen-right.

use super::math::{noise3, rad, smoothstep, v3, M3, V3};
use super::skeleton::{Joints, Pins};
use super::sweep::{key, Key, Sweep};

/// Centre of the chair's footprint on the floor.
pub const CHAIR_POS: V3 = v3(-1.2, 0.0, 0.0);
/// Where he stops (floor point between his feet), measured from CHAIR_POS along +X.
pub const STOP_OFFSET: f32 = 0.72;

/// Surface materials; shading lives in `render.rs`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mat {
    Leather,
    Wood,
    /// Suit cloth: sleeves, trousers.
    Suit,
    /// Jacket body: [`Scene::refine`] paints its front with lapels, shirt and tie.
    Jacket,
    Lapel,
    /// Dark detail on the jacket: button, the edge of the fronts.
    Seam,
    Shirt,
    Tie,
    Skin,
    Hair,
    /// Front of the head: [`Scene::refine`] paints eyes, brows and lips.
    Face,
    Eye,
    Lips,
    /// Skull: [`Scene::refine`] splits it into hair (top, back) and skin (face side).
    Head,
    Shoes,
}

#[derive(Clone, Debug)]
pub enum Shape {
    /// Spline-fitted surface along a joint chain (limbs, torso): see `sweep.rs`.
    Sweep(Box<Sweep>),
    Sphere {
        c: V3,
        r: f32,
    },
    Capsule {
        a: V3,
        b: V3,
        r: f32,
    },
    /// Capsule whose radius changes linearly from `ra` at `a` to `rb` at `b`.
    Cone {
        a: V3,
        b: V3,
        ra: f32,
        rb: f32,
    },
    /// Ellipsoid: centre, inverse rotation (world -> local), radii. The distance is a close
    /// approximation, accurate near the surface.
    Ellipsoid {
        c: V3,
        inv: M3,
        r: V3,
    },
    /// Hair mass: an ellipsoid (centre `off`, radii `r`, in head units) clipped to the hairline,
    /// so it starts exactly where the painted hairline does. Head centre, world -> head rotation,
    /// head scale.
    Cap {
        c: V3,
        inv: M3,
        off: V3,
        r: V3,
        scale: f32,
    },
    /// Rounded box: centre, inverse rotation (world -> local), outer half extents, edge radius.
    RBox {
        c: V3,
        inv: M3,
        half: V3,
        r: f32,
    },
}

/// Distance band beyond a rough surface over which its displacement fades out (wider than any
/// smooth-union blend, so blends always see the displaced surface).
const ROUGH_FADE: (f32, f32) = (0.055, 0.085);

/// Surface irregularity: noise displacement in a frame attached to the part, so it moves with
/// it (cloth folds stay on the sleeve, creases stay in the leather).
#[derive(Clone, Copy, Debug)]
pub struct Rough {
    /// Origin and world -> local rotation of the noise frame.
    pub anchor: V3,
    pub inv: M3,
    /// Fine detail (wrinkles, creases, strands): frequency per local axis (1/m), amplitude (m).
    pub freq: V3,
    pub amp: f32,
    /// Broad unevenness (lumps, sag): frequency (1/m), amplitude (m).
    pub lump_freq: f32,
    pub lump: f32,
    /// Decorrelates parts that share a frame.
    pub seed: f32,
}

impl Rough {
    fn reach(&self) -> f32 {
        self.amp + self.lump
    }

    /// Displacement at local (part-attached) coordinates `q`.
    fn displace(&self, q: V3) -> f32 {
        let o = v3(self.seed * 7.31, self.seed * 3.17, self.seed * 5.73);
        let f = self.freq;
        let fine = noise3(v3(q.x * f.x, q.y * f.y, q.z * f.z) + o);
        let lump = noise3(q * self.lump_freq + o * 1.9 + v3(11.0, 4.0, 7.0));
        self.amp * fine + self.lump * lump
    }
}

#[derive(Clone, Debug)]
pub struct Prim {
    pub shape: Shape,
    pub mat: Mat,
    /// Smooth-union radius used when this primitive joins the ones before it (0 = the group's).
    pub blend: f32,
    pub rough: Option<Rough>,
}

pub fn sd_capsule(p: V3, a: V3, b: V3, r: f32) -> f32 {
    let pa = p - a;
    let ba = b - a;
    let bb = ba.dot(ba);
    let h = if bb > 1e-12 {
        (pa.dot(ba) / bb).clamp(0.0, 1.0)
    } else {
        0.0
    };
    (pa - ba * h).length() - r
}

/// Round cone (capsule with different end radii), exact distance.
pub fn sd_round_cone(p: V3, a: V3, b: V3, ra: f32, rb: f32) -> f32 {
    let ba = b - a;
    let l2 = ba.dot(ba);
    if l2 < 1e-10 {
        return (p - a).length() - ra.max(rb);
    }
    let rr = ra - rb;
    let a2 = l2 - rr * rr;
    if a2 <= 0.0 {
        // one end sphere contains the other
        return ((p - a).length() - ra).min((p - b).length() - rb);
    }
    let il2 = 1.0 / l2;
    let pa = p - a;
    let y = pa.dot(ba);
    let z = y - l2;
    let xv = pa * l2 - ba * y;
    let x2 = xv.dot(xv);
    let y2 = y * y * l2;
    let z2 = z * z * l2;
    let k = rr.signum() * rr * rr * x2;
    if z.signum() * a2 * z2 > k {
        return (x2 + z2).sqrt() * il2 - rb;
    }
    if y.signum() * a2 * y2 < k {
        return (x2 + y2).sqrt() * il2 - ra;
    }
    ((x2 * a2 * il2).sqrt() + y * rr) * il2 - ra
}

/// Ellipsoid with radii `r` (bound-style approximation that is exact on the surface).
pub fn sd_ellipsoid(p: V3, r: V3) -> f32 {
    let q = v3(p.x / r.x, p.y / r.y, p.z / r.z);
    let k0 = q.length();
    let k1 = v3(q.x / r.x, q.y / r.y, q.z / r.z).length();
    if k1 < 1e-9 {
        return -r.x.min(r.y).min(r.z);
    }
    k0 * (k0 - 1.0) / k1
}

/// Box with outer half extents `half` and edges rounded by `r` (r is clamped to fit).
pub fn sd_round_box(p: V3, half: V3, r: f32) -> f32 {
    let r = r.min(half.x).min(half.y).min(half.z);
    let q = p.abs() - half + V3::splat(r);
    q.max_s(0.0).length() + q.max_comp().min(0.0) - r
}

/// Polynomial smooth minimum: blends surfaces within distance `k`.
pub fn smin(a: f32, b: f32, k: f32) -> f32 {
    if k <= 0.0 {
        return a.min(b);
    }
    let h = (0.5 + 0.5 * (b - a) / k).clamp(0.0, 1.0);
    b + (a - b) * h - k * h * (1.0 - h)
}

impl Prim {
    /// Joins the primitives before it with a wider smooth union.
    fn soft(self, blend: f32) -> Prim {
        Prim { blend, ..self }
    }

    /// Adds surface irregularity.
    fn rough(self, r: Rough) -> Prim {
        Prim {
            rough: Some(r),
            ..self
        }
    }

    /// Distance without the surface irregularity (for shadow and occlusion estimates, which
    /// don't need millimetre detail; the shading normals still carry it).
    pub fn dist_coarse(&self, p: V3) -> f32 {
        match &self.shape {
            Shape::Sweep(sw) => sw.eval(p).0,
            _ => self.base_dist(p),
        }
    }

    pub fn dist(&self, p: V3) -> f32 {
        // sweeps provide their own surface-attached coordinates for the noise
        let (d, local) = match &self.shape {
            Shape::Sweep(sw) => {
                let (d, q) = sw.eval(p);
                (d, Some(q))
            }
            _ => (self.base_dist(p), None),
        };
        let Some(r) = &self.rough else { return d };
        // The displacement fades out with distance (it only shapes the surface), giving way to a
        // safe lower bound far away. The fade is continuous: a cut-off would show up as seam
        // lines wherever this primitive smooth-blends with a neighbour.
        let w = 1.0 - smoothstep(ROUGH_FADE.0, ROUGH_FADE.1, d - r.reach());
        if w <= 0.0 {
            return d - r.reach();
        }
        let q = local.unwrap_or_else(|| r.inv * (p - r.anchor));
        d + w * r.displace(q) - (1.0 - w) * r.reach()
    }

    fn base_dist(&self, p: V3) -> f32 {
        match self.shape {
            Shape::Sweep(ref sw) => sw.eval(p).0,
            Shape::Sphere { c, r } => (p - c).length() - r,
            Shape::Capsule { a, b, r } => sd_capsule(p, a, b, r),
            Shape::Cone { a, b, ra, rb } => sd_round_cone(p, a, b, ra, rb),
            Shape::Ellipsoid { c, inv, r } => sd_ellipsoid(inv * (p - c), r),
            Shape::Cap {
                c,
                inv,
                off,
                r,
                scale,
            } => {
                let u = inv * (p - c) / scale;
                let shell = sd_ellipsoid(u - off, r);
                let above = hairline(u.z.abs().atan2(u.x)) - u.y;
                shell.max(above) * scale
            }
            Shape::RBox { c, inv, half, r } => sd_round_box(inv * (p - c), half, r),
        }
    }

    /// Bounding sphere (centre, radius).
    fn bound(&self) -> (V3, f32) {
        let (c, r) = self.shape_bound();
        (c, r + self.rough.as_ref().map_or(0.0, |r| r.reach()))
    }

    fn shape_bound(&self) -> (V3, f32) {
        match self.shape {
            Shape::Sweep(ref sw) => sw.bound(),
            Shape::Sphere { c, r } => (c, r),
            Shape::Capsule { a, b, r } => ((a + b) * 0.5, (b - a).length() * 0.5 + r),
            Shape::Cone { a, b, ra, rb } => ((a + b) * 0.5, (b - a).length() * 0.5 + ra.max(rb)),
            Shape::Ellipsoid { c, r, .. } => (c, r.max_comp()),
            Shape::Cap {
                c,
                inv,
                off,
                r,
                scale,
            } => (c + inv.transpose() * off * scale, r.max_comp() * scale),
            Shape::RBox { c, half, .. } => (c, half.length()),
        }
    }
}

fn sphere(c: V3, r: f32, mat: Mat) -> Prim {
    Prim {
        shape: Shape::Sphere { c, r },
        mat,
        blend: 0.0,
        rough: None,
    }
}
fn capsule(a: V3, b: V3, r: f32, mat: Mat) -> Prim {
    Prim {
        shape: Shape::Capsule { a, b, r },
        mat,
        blend: 0.0,
        rough: None,
    }
}
fn cone(a: V3, b: V3, ra: f32, rb: f32, mat: Mat) -> Prim {
    Prim {
        shape: Shape::Cone { a, b, ra, rb },
        mat,
        blend: 0.0,
        rough: None,
    }
}
fn ellipsoid(c: V3, rot: M3, r: V3, mat: Mat) -> Prim {
    Prim {
        shape: Shape::Ellipsoid {
            c,
            inv: rot.transpose(),
            r,
        },
        mat,
        blend: 0.0,
        rough: None,
    }
}
fn cap(j: &Joints, off: V3, r: V3) -> Prim {
    Prim {
        shape: Shape::Cap {
            c: j.head,
            inv: j.head_frame.transpose(),
            off,
            r,
            scale: HEAD_SCALE,
        },
        mat: Mat::Hair,
        blend: 0.0,
        rough: None,
    }
}

/// Leather: soft lumps where the padding is uneven, fine creases. World frame (the chair is still).
fn leather(seed: f32, lump: f32) -> Rough {
    Rough {
        anchor: CHAIR_POS,
        inv: M3::I,
        freq: v3(24.0, 30.0, 24.0),
        amp: 0.0011,
        lump_freq: 4.5,
        lump,
        seed,
    }
}

/// Frame for a limb segment a -> b: local y along the bone, x toward `toward`.
fn bone_frame(a: V3, b: V3, toward: V3) -> M3 {
    let y = (b - a).normalize();
    let x = perp_to(y, toward, V3::Y.cross(y) + V3::X * 1e-3);
    M3::from_cols(x, y, x.cross(y))
}

/// Suit cloth on a part with frame `frame` at `anchor` (local y = the long axis): soft, broad
/// unevenness and faint creases (not regular rings, which read as a ribbed tube).
fn cloth(anchor: V3, frame: M3, seed: f32) -> Rough {
    Rough {
        anchor,
        inv: frame.transpose(),
        freq: v3(19.0, 13.0, 19.0),
        amp: 0.0013,
        lump_freq: 6.0,
        lump: 0.0022,
        seed,
    }
}

fn rbox(c: V3, rot: M3, half: V3, r: f32, mat: Mat) -> Prim {
    Prim {
        shape: Shape::RBox {
            c,
            inv: rot.transpose(),
            half,
            r,
        },
        mat,
        blend: 0.0,
        rough: None,
    }
}

/// A set of primitives blended with a smooth union, with a bounding sphere for culling.
#[derive(Clone, Debug)]
pub struct Group {
    pub prims: Vec<Prim>,
    /// Bounding sphere of each primitive, for skipping the ones too far away to matter.
    bounds: Vec<(V3, f32)>,
    pub blend: f32,
    pub center: V3,
    pub radius: f32,
}

impl Group {
    pub fn new(prims: Vec<Prim>, blend: f32) -> Group {
        let n = prims.len().max(1) as f32;
        let center = prims.iter().fold(V3::ZERO, |acc, p| acc + p.bound().0) / n;
        let radius = prims
            .iter()
            .map(|p| {
                let (c, r) = p.bound();
                (c - center).length() + r
            })
            .fold(0.0f32, f32::max)
            + prims.iter().map(|p| p.blend).fold(blend, f32::max);
        Group {
            bounds: prims.iter().map(|p| p.bound()).collect(),
            prims,
            blend,
            center,
            radius,
        }
    }

    /// Distance and material of the nearest primitive (material of the closest one).
    pub fn eval(&self, p: V3) -> (f32, Mat) {
        self.eval_with(p, true)
    }

    /// Distance only, without surface irregularity: see [`Prim::dist_coarse`].
    pub fn eval_coarse(&self, p: V3) -> f32 {
        self.eval_with(p, false).0
    }

    fn eval_with(&self, p: V3, detail: bool) -> (f32, Mat) {
        let mut d = f32::MAX;
        let mut best = f32::MAX;
        let mut mat = Mat::Leather;
        for (prim, &(c, r)) in self.prims.iter().zip(&self.bounds) {
            let k = if prim.blend > 0.0 {
                prim.blend
            } else {
                self.blend
            };
            // a primitive at least `k` farther than the running distance cannot change the
            // smooth union (smin returns `d` exactly), so its exact distance is not needed
            if d != f32::MAX && (p - c).length() - r >= d + k {
                continue;
            }
            let di = if detail {
                prim.dist(p)
            } else {
                prim.dist_coarse(p)
            };
            if di < best {
                best = di;
                mat = prim.mat;
            }
            d = if d == f32::MAX { di } else { smin(d, di, k) };
        }
        (d, mat)
    }
}

/// How much the chair is pressed in this frame.
#[derive(Clone, Copy, Debug, Default)]
pub struct ChairState {
    /// Seat cushion compression, metres.
    pub seat_squash: f32,
    /// Back cushion compression, metres.
    pub back_squash: f32,
}

// ---- Chair geometry (local frame: origin on the floor at CHAIR_POS, front = +X) --------------
const SEAT_TOP: f32 = 0.47;
// Armrests of the Blender film's chair (Poly Haven mid_century_lounge_chair): the shell's sides,
// their top at 0.70 m, 0.43 m either side of the middle, reaching 0.45 m in front of it.
const ARM_Z: f32 = 0.49; // armrest centreline from the middle
const ARM_ROLL_Y: f32 = 0.56; // centre of the armrest top
const ARM_ROLL_R: f32 = 0.04;
const ARM_FRONT_X: f32 = 0.36; // front end of the armrest
const BACK_TILT: f32 = 8.0; // degrees the backrest leans back
const CUSHION_TILT: f32 = 10.0;

/// Builds the armchair: a deep leather club chair with rolled arms and a tall padded back.
pub fn chair(st: &ChairState) -> Group {
    let o = CHAIR_POS;
    let at = |x: f32, y: f32, z: f32| o + v3(x, y, z);
    let sq = st.seat_squash.clamp(0.0, 0.04);
    let bsq = st.back_squash.clamp(0.0, 0.04);
    let mut p = vec![
        // skirt / base
        rbox(
            at(0.0, 0.205, 0.0),
            M3::I,
            v3(0.40, 0.135, 0.42),
            0.035,
            Mat::Leather,
        )
        .rough(leather(1.0, 0.004)),
        // seat cushion: compresses from the top and bulges a little sideways; sits a touch
        // askew, as cushions do
        rbox(
            at(0.05, 0.395 - sq * 0.5, 0.006),
            M3::rot_y(rad(1.2)),
            v3(0.36 + sq * 0.3, 0.075 - sq * 0.5, 0.29 + sq * 0.3),
            0.045,
            Mat::Leather,
        )
        .rough(leather(2.0, 0.007)),
        // backrest frame and its padded top roll
        rbox(
            at(-0.33, 0.70, 0.0),
            M3::rot_z(rad(BACK_TILT)),
            v3(0.10, 0.40, 0.45),
            0.065,
            Mat::Leather,
        )
        .rough(leather(3.0, 0.005)),
        capsule(
            at(-0.37, 1.05, -0.40),
            at(-0.37, 1.052, 0.40),
            0.105,
            Mat::Leather,
        )
        .rough(leather(4.0, 0.008)),
        // back cushion: compresses when he leans into it
        rbox(
            at(-0.20 - bsq * 0.5, 0.73, -0.005),
            M3::rot_z(rad(CUSHION_TILT)) * M3::rot_x(rad(1.5)),
            v3(0.075 - bsq * 0.5, 0.25, 0.29),
            0.055,
            Mat::Leather,
        )
        .rough(leather(5.0, 0.008)),
    ];
    for s in [-1.0f32, 1.0] {
        // armrest body and the thick rolled top; the right one is a little fuller (worn in)
        let side = if s < 0.0 { 0.0 } else { 1.0 };
        p.push(
            rbox(
                at(0.01, 0.42, s * ARM_Z),
                M3::I,
                v3(0.40, 0.20, 0.075),
                0.045,
                Mat::Leather,
            )
            .rough(leather(6.0 + side, 0.005)),
        );
        p.push(
            capsule(
                at(-0.36, ARM_ROLL_Y + 0.002 * side, s * ARM_Z),
                at(ARM_FRONT_X, ARM_ROLL_Y, s * ARM_Z),
                ARM_ROLL_R + 0.003 * side,
                Mat::Leather,
            )
            .rough(leather(8.0 + side, 0.006)),
        );
        // short wooden feet
        for x in [-0.33f32, 0.33] {
            p.push(rbox(
                at(x, 0.04, s * 0.35),
                M3::I,
                v3(0.035, 0.045, 0.035),
                0.012,
                Mat::Wood,
            ));
        }
    }
    Group::new(p, 0.03)
}

/// Contact points on the chair for the IK pins (feet are filled in by the timeline).
pub fn chair_pins() -> Pins {
    let o = CHAIR_POS;
    let hand_r = 0.045;
    let mut pins = Pins::none();
    for (i, s) in [-1.0f32, 1.0].into_iter().enumerate() {
        // gripping the front of the roll, a little inboard of its crest
        pins.grip[i] = o + v3(
            ARM_FRONT_X - 0.07,
            ARM_ROLL_Y + ARM_ROLL_R + hand_r * 0.7,
            s * (ARM_Z - 0.015),
        );
        // forearm resting on the inner half of the roll; hand centre ends near the front
        let z = ARM_Z - 0.04;
        let top = ARM_ROLL_Y + (ARM_ROLL_R * ARM_ROLL_R - 0.04 * 0.04).sqrt();
        pins.rest[i] = (o + v3(ARM_FRONT_X + 0.03, top + 0.04, s * z), -V3::X);
    }
    pins
}

/// Seat cushion top at rest, for the timeline's contact timing and tests.
pub fn seat_top() -> f32 {
    SEAT_TOP
}

// ---- The man: a gentleman in a dark two-piece suit, white shirt and tie ------------------------
// Local frames follow the skeleton: x forward, y up, z his right.

/// Jacket V opening: depth below the neck base where the lapels meet (the button), metres.
const V_DEPTH: f32 = 0.28;
/// Half-width of the shirt showing at the collar, and of each lapel at the top.
const V_HALF: f32 = 0.072;
const LAPEL_W: f32 = 0.045;
/// The tie hangs a few millimetres to his right of centre.
const TIE_OFF: f32 = 0.004;

/// Unit vector perpendicular to `axis`, as close to `want` as possible (`alt` if parallel).
fn perp_to(axis: V3, want: V3, alt: V3) -> V3 {
    let v = want - axis * want.dot(axis);
    if v.length() > 1e-4 {
        v.normalize()
    } else {
        (alt - axis * alt.dot(axis)).normalize()
    }
}

/// Builds the man's body (everything but the head) from the solved skeleton: torso and arms,
/// and the legs as a second group (bounded separately, so rays that miss the legs skip them).
pub fn man(j: &Joints) -> (Group, Group) {
    let t = |v: V3| j.trunk * v;
    let c = |v: V3| j.chest * v;
    let mut legs: Vec<Prim> = Vec::new();
    // the torso is one spline-fitted surface from the jacket hem up to the shoulder line
    let torso = Sweep::new(
        &[
            j.pelvis + t(v3(0.0, -0.135, 0.0)),
            j.pelvis + t(v3(0.0, 0.04, 0.0)),
            j.spine_mid,
            j.spine_mid + c(v3(0.0, 0.13, 0.0)),
            j.neck + c(v3(-0.012, -0.045, 0.0)),
        ],
        &[
            j.trunk.col(0),
            j.trunk.col(0),
            j.trunk.col(0),
            j.chest.col(0),
            j.chest.col(0),
        ],
        &TORSO_KEYS,
        0.8,
        (0.22, 1.0),
    );
    let mut p = vec![sweep(torso, Mat::Jacket).rough(cloth(V3::ZERO, M3::I, 11.0))];
    for s in [-1.0f32, 1.0] {
        // shoulder blades round out the upper back
        p.push(
            ellipsoid(
                j.spine_mid + c(v3(-0.055, 0.125, s * 0.075)),
                j.chest,
                v3(0.05, 0.085, 0.07),
                Mat::Jacket,
            )
            .soft(0.05)
            .rough(cloth(j.spine_mid, j.chest, 16.0 + s)),
        );
    }
    // trousers: pelvis below the jacket hem, and the seat behind the hip joints
    legs.push(
        ellipsoid(
            j.pelvis + t(v3(0.0, -0.01, 0.0)),
            j.trunk,
            v3(0.104, 0.102, 0.158),
            Mat::Suit,
        )
        .rough(cloth(j.pelvis, j.trunk, 15.0)),
    );
    for s in [-1.0f32, 1.0] {
        legs.push(
            ellipsoid(
                j.pelvis + t(v3(-0.055, -0.045, s * 0.066)),
                j.trunk,
                v3(0.074, 0.09, 0.078),
                Mat::Suit,
            )
            .soft(0.03)
            .rough(cloth(j.pelvis, j.trunk, 18.0 + s)),
        );
    }
    // neck, shirt collar and the tie knot
    p.push(cone(
        j.neck,
        j.neck_top + j.head_frame * v3(0.0, 0.02, 0.0),
        0.058,
        0.05,
        Mat::Skin,
    ));
    let collar_a = j.neck + c(v3(0.004, -0.012, 0.0));
    let collar_b = j.neck + j.head_frame * v3(0.0, 0.066, 0.0);
    p.push(cone(collar_a, collar_b, 0.067, 0.06, Mat::Shirt));
    p.push(sphere(
        collar_a.lerp(collar_b, 0.4) + j.head_frame.col(0) * 0.05 + j.chest.col(2) * TIE_OFF,
        0.019,
        Mat::Tie,
    ));
    let right = j.body.col(2);
    let fwd_body = j.body.col(0);
    for side in 0..2 {
        let sd = side as f32;
        let s = if side == 0 { -1.0 } else { 1.0 };
        // sleeves, a little shirt cuff, the hand
        let fore = (j.hand[side] - j.elbow[side]).normalize();
        let wrist = j.hand[side] - fore * 0.065;
        // sleeve head over the deltoid, then the whole sleeve as one spline-fitted surface
        let upper = bone_frame(j.shoulder[side], j.elbow[side], fwd_body);
        p.push(
            ellipsoid(
                j.shoulder[side] + upper.col(1) * 0.03,
                upper,
                v3(0.056, 0.064, 0.056),
                Mat::Suit,
            )
            .soft(0.04)
            .rough(cloth(j.shoulder[side], upper, 20.0 + sd)),
        );
        let cuff = wrist - fore * 0.012;
        // the inside of the elbow is the front of the arm
        let elbow_front = bend_side(j.shoulder[side], j.elbow[side], cuff, -1.0, fwd_body);
        let arm = Sweep::new(
            &[j.shoulder[side], j.elbow[side], cuff],
            &[fwd_body, elbow_front, elbow_front],
            &ARM_KEYS,
            0.7,
            (1.0, 0.45),
        );
        p.push(
            sweep(arm, Mat::Suit)
                .soft(0.035)
                .rough(cloth(V3::ZERO, M3::I, 21.0 + sd)),
        );
        p.push(capsule(
            wrist - fore * 0.022,
            wrist - fore * 0.006,
            0.035,
            Mat::Shirt,
        ));
        // palm faces his thigh when the forearm hangs, the floor when it lies flat
        let flat = 1.0 - fore.y.abs();
        let palm_n = perp_to(fore, -(right * s) * (1.0 - flat) - V3::Y * flat, right);
        let across = palm_n.cross(fore);
        p.push(rbox(
            wrist + fore * 0.045,
            M3::from_cols(palm_n, fore, across),
            v3(0.016, 0.045, 0.04),
            0.015,
            Mat::Skin,
        ));
        // fingers curl toward the palm: relaxed when hanging, over the armrest when flat
        let curl = rad(25.0 + 35.0 * flat);
        let fd = fore * curl.cos() + palm_n * curl.sin();
        let fn_ = perp_to(fd, palm_n, across);
        p.push(rbox(
            wrist + fore * 0.084 + fd * 0.038,
            M3::from_cols(fn_, fd, fn_.cross(fd)),
            v3(0.013, 0.04, 0.037),
            0.012,
            Mat::Skin,
        ));
        // thumb on the forward/inner edge of the hand
        let thumb = across * -s;
        p.push(capsule(
            wrist + fore * 0.02 + thumb * 0.028,
            wrist + fore * 0.068 + thumb * 0.042 + palm_n * 0.018,
            0.012,
            Mat::Skin,
        ));
        // trouser legs: full thigh tapering to a straight leg that breaks on the shoe
        // the trouser leg, hip to ankle, as one spline-fitted surface; the knee points forward
        let knee_front = bend_side(j.hip[side], j.knee[side], j.ankle[side], 1.0, fwd_body);
        let leg = Sweep::new(
            &[j.hip[side], j.knee[side], j.ankle[side]],
            &[fwd_body, knee_front, knee_front],
            &LEG_KEYS,
            0.7,
            (1.0, 0.45),
        );
        legs.push(
            sweep(leg, Mat::Suit)
                .soft(0.03)
                .rough(cloth(V3::ZERO, M3::I, 25.0 + sd)),
        );
        // oxford shoe: low, long, rounded toe
        let fwd = (j.toe[side] - j.heel[side]).normalize();
        let up = perp_to(fwd, j.knee[side] - j.ankle[side], V3::Y);
        legs.push(rbox(
            (j.heel[side] + j.toe[side]) * 0.5 + fwd * 0.016,
            M3::from_cols(fwd, up, fwd.cross(up)),
            v3(0.126, 0.04, 0.046),
            0.034,
            Mat::Shoes,
        ));
    }
    (Group::new(p, 0.02), Group::new(legs, 0.02))
}

// Cross-section profiles, fitted with smooth curves along each joint chain: position (0 = first
// joint, 1 = second, ...), front-to-back and side-to-side radius, centre offset toward the front
// and outward, metres. Muscles don't sit centred on the bone, and the cloth follows them.

/// Jacket hem (0), hips (1), waist, mid back (2), chest (3), shoulder line (4).
/// Athletic and lean: full chest and broad shoulders over a trim waist (a gentle V).
const TORSO_KEYS: [Key; 7] = [
    key(0.0, 0.114, 0.164, 0.004, 0.0),
    key(1.0, 0.11, 0.16, 0.004, 0.0),
    key(1.65, 0.104, 0.148, 0.008, 0.0),
    key(2.0, 0.11, 0.156, 0.011, 0.0),
    key(3.0, 0.128, 0.184, 0.018, 0.0),
    key(3.45, 0.12, 0.204, 0.008, 0.0),
    key(4.0, 0.088, 0.214, -0.018, 0.0),
];
/// Hip (0), front of the thigh, knee (1), calf, ankle (2); trousers fall nearly straight from
/// the knee.
const LEG_KEYS: [Key; 6] = [
    key(0.0, 0.096, 0.097, -0.006, 0.005),
    key(0.35, 0.097, 0.09, 0.013, 0.005),
    key(0.8, 0.076, 0.073, 0.007, 0.0),
    key(1.0, 0.068, 0.067, 0.004, 0.0),
    key(1.3, 0.067, 0.063, -0.01, 0.0),
    key(2.0, 0.057, 0.054, 0.004, 0.0),
];
/// Shoulder (0), biceps, elbow (1), forearm, cuff (2).
const ARM_KEYS: [Key; 5] = [
    key(0.0, 0.06, 0.061, 0.0, 0.005),
    key(0.4, 0.059, 0.055, 0.007, 0.0),
    key(1.0, 0.05, 0.051, -0.002, 0.0),
    key(1.3, 0.052, 0.051, -0.004, 0.003),
    key(2.0, 0.044, 0.043, 0.0, 0.0),
];

fn sweep(sw: Sweep, mat: Mat) -> Prim {
    Prim {
        shape: Shape::Sweep(Box::new(sw)),
        mat,
        blend: 0.0,
        rough: None,
    }
}

/// Direction the middle joint of `a -> m -> b` sticks out to (`sign` 1), or the inside of the
/// bend (`sign` -1); `straight` when the chain is nearly straight.
fn bend_side(a: V3, m: V3, b: V3, sign: f32, straight: V3) -> V3 {
    let off = m - (a + b) * 0.5;
    let w = smoothstep(0.01, 0.06, off.length());
    if w <= 0.0 {
        return straight;
    }
    straight.lerp(off.normalize() * sign, w).normalize()
}

/// Builds the head: cranium with hair, face tapering to the chin, brow, nose, ears.
pub fn head(j: &Joints) -> Group {
    let f = j.head_frame;
    let at = |x: f32, y: f32, z: f32| j.head + f * (v3(x, y, z) * HEAD_SCALE);
    let r = |x: f32, y: f32, z: f32| v3(x, y, z) * HEAD_SCALE;
    let mut p = vec![
        ellipsoid(at(-0.016, 0.02, 0.0), f, r(0.1, 0.106, 0.081), Mat::Head),
        // a neat professional cut: close at the sides and back, a little length on top, combed
        // over to his right from a parting on his left
        cap(j, v3(-0.017, 0.023, 0.002), v3(0.105, 0.111, 0.0845)).rough(hair(j, 31.0)),
        cap(j, v3(0.012, 0.074, 0.01), v3(0.086, 0.044, 0.07)).rough(hair(j, 32.0)),
        // face: cheeks and a firm jaw, then the chin
        ellipsoid(at(0.018, -0.03, 0.0), f, r(0.08, 0.086, 0.073), Mat::Face),
        ellipsoid(at(0.057, -0.086, 0.0), f, r(0.036, 0.03, 0.04), Mat::Face),
        // a modest nose, blended in softly so it shapes the face without a hard shadow
        cone(
            at(0.088, 0.006, 0.0),
            at(0.097, -0.018, 0.0),
            0.008,
            0.011,
            Mat::Skin,
        )
        .soft(0.02),
    ];
    for s in [-1.0f32, 1.0] {
        p.push(ellipsoid(
            at(-0.014, -0.008, s * 0.08),
            f,
            r(0.024, 0.032, 0.011),
            Mat::Skin,
        ));
    }
    Group::new(p, 0.022 * HEAD_SCALE)
}

/// Combed hair: strands run front to back, so detail is fine across them and long along them;
/// clumps break up the outline.
fn hair(j: &Joints, seed: f32) -> Rough {
    Rough {
        anchor: j.head,
        inv: j.head_frame.transpose(),
        freq: v3(12.0, 45.0, 80.0),
        amp: 0.0016,
        lump_freq: 24.0,
        lump: 0.0022,
        seed,
    }
}

/// Head size relative to the skeleton's (the face reads better a touch larger at terminal size).
const HEAD_SCALE: f32 = 1.06;

/// Hairline height (head frame, metres above the head centre) by angle around the head:
/// 0 = straight ahead, pi = the back. Short, neat cut: high forehead, receding a little at the
/// temples, down to the top of the ears at the sides, tapered to the nape.
fn hairline(angle: f32) -> f32 {
    const PTS: [(f32, f32); 6] = [
        (0.0, 0.068),
        (0.45, 0.072),
        (0.95, 0.05),
        (1.45, 0.03),
        (2.0, -0.02),
        (std::f32::consts::PI, -0.07),
    ];
    let a = angle.clamp(0.0, std::f32::consts::PI);
    for w in PTS.windows(2) {
        let ((a0, h0), (a1, h1)) = (w[0], w[1]);
        if a <= a1 {
            return h0 + (h1 - h0) * ((a - a0) / (a1 - a0));
        }
    }
    PTS[PTS.len() - 1].1
}

/// Everything the renderer needs for one frame.
#[derive(Clone, Debug)]
pub struct Scene {
    pub groups: Vec<Group>,
    /// Head centre and frame (column 0 = where his face points).
    pub head: (V3, M3),
    /// Neck base and chest frame: where the jacket's V opening, shirt and tie are.
    pub chest: (V3, M3),
}

impl Scene {
    pub fn new(chair_state: &ChairState, joints: &Joints) -> Scene {
        Scene {
            groups: {
                let (body, legs) = man(joints);
                vec![chair(chair_state), body, legs, head(joints)]
            },
            head: (joints.head, joints.head_frame),
            chest: (joints.neck, joints.chest),
        }
    }

    /// Final material at surface point `p`: splits the skull into hair and face, and paints the
    /// jacket front with lapels, the shirt opening, the tie and a pocket square.
    pub fn refine(&self, m: Mat, p: V3) -> Mat {
        match m {
            Mat::Head => {
                let (c, f) = self.head;
                let rel = f.transpose() * (p - c) / HEAD_SCALE;
                let angle = rel.z.abs().atan2(rel.x);
                let line = hairline(angle);
                if rel.y < line {
                    self.refine(Mat::Face, p)
                } else {
                    Mat::Hair
                }
            }
            Mat::Face => {
                // eyes, brows and lips, painted where the face looks forward
                let (c, f) = self.head;
                let rel = f.transpose() * (p - c) / HEAD_SCALE;
                if rel.x < 0.04 {
                    return Mat::Skin;
                }
                let ez = rel.z.abs() - 0.033;
                let eye = (ez / 0.015).powi(2) + ((rel.y - 0.008) / 0.0075).powi(2);
                let brow = (ez / 0.02).powi(2) + ((rel.y - 0.03) / 0.0045).powi(2);
                let lips = (rel.z / 0.024).powi(2) + ((rel.y + 0.052) / 0.006).powi(2);
                if eye < 1.0 {
                    Mat::Eye
                } else if brow < 1.0 {
                    Mat::Hair
                } else if lips < 1.0 {
                    Mat::Lips
                } else {
                    Mat::Skin
                }
            }
            Mat::Jacket => {
                let (neck, chest) = self.chest;
                let l = chest.transpose() * (p - neck);
                let dy = -l.y;
                let az = l.z.abs();
                if l.x < 0.02 || !(-0.08..V_DEPTH + 0.2).contains(&dy) {
                    return Mat::Suit;
                }
                if l.x > 0.03 && (-0.15..-0.095).contains(&l.z) && (0.15..0.172).contains(&dy) {
                    return Mat::Shirt; // pocket square, his left breast
                }
                let k = 1.0 - dy.max(0.0) / V_DEPTH;
                if dy > V_DEPTH - 0.012 {
                    // the button, then the fronts curving open toward the hem
                    let b = dy - V_DEPTH;
                    let open = (b * 0.45).max(0.0);
                    if (b * b + az * az).sqrt() < 0.011 || (az - open).abs() < 0.0045 && b > 0.01 {
                        return Mat::Seam;
                    }
                    return Mat::Suit;
                }
                if (l.z - TIE_OFF).abs() < 0.016 + 0.008 * (1.0 - k) && dy > 0.03 {
                    Mat::Tie
                } else if az < V_HALF * k {
                    Mat::Shirt
                } else if az < (V_HALF + LAPEL_W) * k {
                    Mat::Lapel
                } else {
                    Mat::Suit
                }
            }
            m => m,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sdf_basics() {
        assert!(
            (sd_capsule(v3(0.0, 1.0, 0.0), V3::ZERO, v3(1.0, 0.0, 0.0), 0.5) - 0.5).abs() < 1e-6
        );
        assert!((sd_round_box(v3(2.0, 0.0, 0.0), V3::splat(1.0), 0.1) - 1.0).abs() < 1e-5);
        assert!(sd_round_box(V3::ZERO, V3::splat(1.0), 0.1) < 0.0);
        assert!(smin(1.0, 1.0, 0.1) < 1.0, "smooth union bulges");
        assert_eq!(smin(1.0, 3.0, 0.1), 1.0);
    }

    #[test]
    fn bounds_contain_the_surface() {
        let g = chair(&ChairState::default());
        // sample points just outside the bounding sphere are outside the chair
        for i in 0..64 {
            let a = i as f32 * 0.7;
            let dir = v3(a.cos(), (a * 0.37).sin(), a.sin()).normalize();
            let p = g.center + dir * (g.radius + 0.01);
            assert!(g.eval(p).0 > 0.0);
        }
    }

    #[test]
    fn seat_and_armrest_heights() {
        let g = chair(&ChairState::default());
        // straight down onto the middle of the seat: surface near SEAT_TOP
        let mut y = 1.2;
        while g.eval(CHAIR_POS + v3(0.05, y, 0.0)).0 > 0.001 && y > 0.0 {
            y -= 0.001;
        }
        assert!((y - SEAT_TOP).abs() < 0.03, "seat top at {y}");
        // grips sit on top of the armrests, not inside or floating
        let pins = chair_pins();
        for g_pt in pins.grip {
            let d = g.eval(g_pt).0;
            assert!(d > 0.0 && d < 0.06, "grip clearance {d}");
        }
        let pressed = chair(&ChairState {
            seat_squash: 0.03,
            back_squash: 0.0,
        });
        assert!(
            pressed.eval(CHAIR_POS + v3(0.05, SEAT_TOP - 0.01, 0.0)).0 > 0.0,
            "squash lowers the seat"
        );
    }
}
