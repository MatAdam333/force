//! Spline-fitted body surfaces. A smooth spline runs through a chain of joints (hip, knee,
//! ankle; or up the spine), and an elliptical cross-section is swept along it whose depth,
//! width and offset from the bone follow smooth curves through a few profile keys. The result
//! is one continuous, curving surface per limb or torso, with no steps between sections.
//!
//! The spline is sampled into a short polyline when the sweep is built (once per frame); the
//! distance function finds the nearest point on it and measures to the interpolated ellipse.

use super::math::{catmull_rom, lerp, monotone_cubic, smoothstep, V3};

/// Axis samples per sweep.
pub const SAMPLES: usize = 16;
/// Beyond this distance outside the nearest section, the sweep reports a cheap lower bound
/// (more than any smooth-union blend, so neighbouring parts never see the difference).
const FAR: f32 = 0.07;
/// Beyond this distance outside its bounding sphere, the sweep isn't evaluated at all.
const FAR_SPHERE: f32 = 0.4;

/// One cross-section profile key: at position `at` along the joint chain (0 = first joint,
/// 1 = second, ...), front-to-back radius `depth`, side-to-side radius `width`, and how far the
/// section's centre sits toward the front (`front`) and outward (`out`) of the bone, metres.
#[derive(Clone, Copy, Debug)]
pub struct Key {
    pub at: f32,
    pub depth: f32,
    pub width: f32,
    pub front: f32,
    pub out: f32,
}

pub const fn key(at: f32, depth: f32, width: f32, front: f32, out: f32) -> Key {
    Key {
        at,
        depth,
        width,
        front,
        out,
    }
}

/// A built sweep: sampled centres, cross-section axes and radii.
#[derive(Clone, Copy, Debug)]
pub struct Sweep {
    c: [V3; SAMPLES],
    /// Unit "front" axis of each cross-section (perpendicular to the spline).
    x: [V3; SAMPLES],
    /// Unit spline tangent at each sample (interpolated between samples, so shading is smooth).
    tan: [V3; SAMPLES],
    rx: [f32; SAMPLES],
    rz: [f32; SAMPLES],
    /// Arc length at each sample (metres from the start), for surface-attached noise.
    arc: [f32; SAMPLES],
    /// How round the two end caps are (1 = as round as the section, small = nearly flat cut).
    caps: (f32, f32),
    /// Per axis segment: direction (end - start) and 1 / its squared length.
    seg: [V3; SAMPLES - 1],
    seg_inv: [f32; SAMPLES - 1],
    /// Bounding sphere.
    bc: V3,
    br: f32,
}

/// Cardinal spline through `pts` (tension: 1 = Catmull-Rom, lower = tighter around joints),
/// evaluated at chain position `u` (0..pts.len()-1). Ends are extrapolated straight.
fn spline(pts: &[V3], tension: f32, u: f32) -> V3 {
    let n = pts.len();
    let i = (u.floor() as usize).min(n - 2);
    let t = u - i as f32;
    let get = |k: isize| -> V3 {
        if k < 0 {
            pts[0] * 2.0 - pts[1]
        } else if k as usize >= n {
            pts[n - 1] * 2.0 - pts[n - 2]
        } else {
            pts[k as usize]
        }
    };
    let (p0, p1, p2, p3) = (
        get(i as isize - 1),
        get(i as isize),
        get(i as isize + 1),
        get(i as isize + 2),
    );
    let m1 = (p2 - p0) * (0.5 * tension);
    let m2 = (p3 - p1) * (0.5 * tension);
    let (t2, t3) = (t * t, t * t * t);
    p1 * (2.0 * t3 - 3.0 * t2 + 1.0)
        + m1 * (t3 - 2.0 * t2 + t)
        + p2 * (-2.0 * t3 + 3.0 * t2)
        + m2 * (t3 - t2)
}

impl Sweep {
    /// Builds a sweep through the joints `pts`. `fronts` gives the direction the front of the
    /// cross-section faces at each joint (interpolated in between); `keys` the profile, in
    /// increasing `at`; `caps` the roundness of the start and end caps.
    pub fn new(pts: &[V3], fronts: &[V3], keys: &[Key], tension: f32, caps: (f32, f32)) -> Sweep {
        let n = pts.len();
        let span = (n - 1) as f32;
        let ats: Vec<f32> = keys.iter().map(|k| k.at).collect();
        let curve = |f: fn(&Key) -> f32, u: f32| {
            let vs: Vec<f32> = keys.iter().map(f).collect();
            monotone_cubic(&ats, &vs, u)
        };
        let mut sw = Sweep {
            c: [V3::ZERO; SAMPLES],
            x: [V3::ZERO; SAMPLES],
            tan: [V3::ZERO; SAMPLES],
            rx: [0.0; SAMPLES],
            rz: [0.0; SAMPLES],
            arc: [0.0; SAMPLES],
            caps,
            seg: [V3::ZERO; SAMPLES - 1],
            seg_inv: [0.0; SAMPLES - 1],
            bc: V3::ZERO,
            br: 0.0,
        };
        let axis: Vec<V3> = (0..SAMPLES)
            .map(|k| spline(pts, tension, span * k as f32 / (SAMPLES - 1) as f32))
            .collect();
        for k in 0..SAMPLES {
            let u = span * k as f32 / (SAMPLES - 1) as f32;
            let tan = if k == 0 {
                axis[1] - axis[0]
            } else if k == SAMPLES - 1 {
                axis[k] - axis[k - 1]
            } else {
                axis[k + 1] - axis[k - 1]
            }
            .normalize();
            let i = (u.floor() as usize).min(n - 2);
            let want = fronts[i].lerp(fronts[i + 1], u - i as f32);
            let mut front = want - tan * want.dot(tan);
            if front.length() < 1e-4 {
                front = tan.cross(V3::Y).cross(tan);
            }
            let front = front.normalize();
            let side = tan.cross(front);
            sw.c[k] = axis[k] + front * curve(|k| k.front, u) + side * curve(|k| k.out, u);
            sw.x[k] = front;
            sw.tan[k] = tan;
            sw.rx[k] = curve(|k| k.depth, u);
            sw.rz[k] = curve(|k| k.width, u);
            if k > 0 {
                sw.arc[k] = sw.arc[k - 1] + (sw.c[k] - sw.c[k - 1]).length();
            }
        }
        for i in 0..SAMPLES - 1 {
            sw.seg[i] = sw.c[i + 1] - sw.c[i];
            sw.seg_inv[i] = 1.0 / sw.seg[i].dot(sw.seg[i]).max(1e-12);
        }
        let mut c = V3::ZERO;
        for v in &sw.c {
            c += *v;
        }
        sw.bc = c / SAMPLES as f32;
        sw.br = (0..SAMPLES)
            .map(|k| (sw.c[k] - sw.bc).length() + sw.rx[k].max(sw.rz[k]))
            .fold(0.0f32, f32::max);
        sw
    }

    /// Signed distance (a close lower bound) and surface-attached coordinates of `p`:
    /// (front, arc length, side), metres.
    pub fn eval(&self, p: V3) -> (f32, V3) {
        // Where along the spline is `p`? Not simply the nearest polyline segment: on the inside
        // of a bend that choice jumps, and the surface with it. Instead the parameters of the
        // nearby segments' closest points are averaged, weighted by how close each is, which
        // varies smoothly everywhere.
        // far outside the bounding sphere (beyond any occlusion probe's reach) the sphere
        // distance is good enough
        let outside = (p - self.bc).length() - self.br;
        if outside > FAR_SPHERE {
            return (outside, V3::ZERO);
        }
        // squared distances first; square roots only for the segments that can matter
        let mut near = [(0.0f32, 0.0f32); SAMPLES - 1];
        let mut d2min = f32::MAX;
        let mut inear = 0;
        for (i, n) in near.iter_mut().enumerate() {
            let ap = p - self.c[i];
            let t = (ap.dot(self.seg[i]) * self.seg_inv[i]).clamp(0.0, 1.0);
            let q = ap - self.seg[i] * t;
            let d2 = q.dot(q);
            *n = (d2, i as f32 + t);
            if d2 < d2min {
                d2min = d2;
                inear = i;
            }
        }
        let dmin = d2min.sqrt();
        // well away from the surface a safe bound is all the marcher needs (this is beyond
        // every smooth-union blend, so neighbouring parts never see the difference)
        let r_near = self.rx[inear]
            .max(self.rz[inear])
            .max(self.rx[inear + 1].max(self.rz[inear + 1]));
        let far = dmin - r_near;
        if far > FAR {
            return (0.9 * far, V3::ZERO);
        }
        const SOFT: f32 = 0.025;
        let (mut wsum, mut usum) = (0.0f32, 0.0f32);
        let cut = dmin + 5.0 * SOFT;
        let cut2 = cut * cut;
        for (d2, u) in near {
            // segments more than a few SOFT farther than the nearest carry no weight
            if d2 > cut2 {
                continue;
            }
            let w = (-(d2.sqrt() - dmin) / SOFT).exp();
            wsum += w;
            usum += w * u;
        }
        // Beyond either end of the sweep: the end cap, `beyond` metres past the end section's
        // plane. Approaching an end, the position eases onto it over the last few centimetres,
        // so the section reaching the cap is exactly the end section (no step).
        const RAMP: f32 = 0.06;
        let last = SAMPLES - 1;
        let past_end = (p - self.c[last]).dot(self.tan[last]);
        let past_start = (self.c[0] - p).dot(self.tan[0]);
        let mut u = (usum / wsum).clamp(0.0, last as f32);
        u = lerp(u, last as f32, smoothstep(-RAMP, 0.0, past_end));
        u = lerp(u, 0.0, smoothstep(-RAMP, 0.0, past_start));
        let beyond = if past_start > 0.0 {
            -past_start
        } else {
            past_end.max(0.0)
        };
        let i = (u.floor() as usize).min(SAMPLES - 2);
        let t = u - i as f32;

        let cr = |v: &[f32; SAMPLES]| {
            let at = |k: isize| v[k.clamp(0, SAMPLES as isize - 1) as usize];
            let i = i as isize;
            catmull_rom(at(i - 1), at(i), at(i + 1), at(i + 2), t)
        };
        let cr3 = |v: &[V3; SAMPLES]| {
            let at = |k: isize| v[k.clamp(0, SAMPLES as isize - 1) as usize];
            let i = i as isize;
            let (a, b, c, d) = (at(i - 1), at(i), at(i + 1), at(i + 2));
            V3 {
                x: catmull_rom(a.x, b.x, c.x, d.x, t),
                y: catmull_rom(a.y, b.y, c.y, d.y, t),
                z: catmull_rom(a.z, b.z, c.z, d.z, t),
            }
        };
        let centre = cr3(&self.c);
        let tan = self.tan[i].lerp(self.tan[i + 1], t).normalize();
        let x = self.x[i].lerp(self.x[i + 1], t);
        let x = (x - tan * x.dot(tan)).normalize();
        let z = tan.cross(x);
        let (rx, rz) = (cr(&self.rx), cr(&self.rz));
        let q = p - centre;
        let (qx, qz) = (q.dot(x), q.dot(z));
        // past the sweep's own ends: the end caps; anywhere else only the cross-section counts
        let round = rx.min(rz);
        let (ry, qy_cap) = if beyond < 0.0 {
            (round * self.caps.0, beyond)
        } else if beyond > 0.0 {
            (round * self.caps.1, beyond)
        } else {
            (round, 0.0)
        };
        let (ux, uy, uz) = (qx / rx, qy_cap / ry, qz / rz);
        let k0 = (ux * ux + uy * uy + uz * uz).sqrt();
        let (vx, vy, vz) = (ux / rx, uy / ry, uz / rz);
        let k1 = (vx * vx + vy * vy + vz * vz).sqrt().max(1e-9);
        // the profile changes along the axis, so the field is scaled down a little to stay a
        // safe distance bound for the ray marcher
        let d = 0.85 * k0 * (k0 - 1.0) / k1;
        let arc = lerp(self.arc[i], self.arc[i + 1], t) + qy_cap;
        // far from the axis the ellipse estimate grows faster than the true distance; the
        // distance to the axis minus the largest radius is always safe
        let rmax = rx.max(rz);
        // (right on the axis the ellipse estimate degenerates to 0: inside is inside)
        let d = if k0 < 1.0 {
            d.min((k0 - 1.0) * round)
        } else {
            d
        };
        (
            d.min((dmin - rmax).max(d * 0.5)),
            V3 {
                x: qx,
                y: arc,
                z: qz,
            },
        )
    }

    /// Bounding sphere (centre, radius).
    pub fn bound(&self) -> (V3, f32) {
        (self.bc, self.br)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::math::v3;

    fn straight() -> Sweep {
        Sweep::new(
            &[v3(0.0, 1.0, 0.0), v3(0.0, 0.0, 0.0)],
            &[V3::X, V3::X],
            &[key(0.0, 0.1, 0.2, 0.0, 0.0), key(1.0, 0.1, 0.2, 0.0, 0.0)],
            1.0,
            (1.0, 1.0),
        )
    }

    #[test]
    fn cross_section_is_the_profile_ellipse() {
        let sw = straight();
        // front radius 0.1 (x), side radius 0.2 (z) at mid height
        assert!(sw.eval(v3(0.1, 0.5, 0.0)).0.abs() < 1e-3);
        assert!(sw.eval(v3(0.0, 0.5, 0.2)).0.abs() < 1e-3);
        assert!(sw.eval(v3(0.0, 0.5, 0.0)).0 < 0.0);
        // far away: a safe (lower) bound, not a wild underestimate; true distance 0.4
        let far = sw.eval(v3(0.5, 0.5, 0.0)).0;
        assert!(far > 0.25 && far <= 0.4, "far {far}");
    }

    #[test]
    fn spline_passes_through_the_joints() {
        let pts = [v3(0.0, 1.0, 0.0), v3(0.3, 0.5, 0.0), v3(0.0, 0.0, 0.0)];
        for (k, p) in pts.iter().enumerate() {
            assert!((spline(&pts, 0.7, k as f32) - *p).length() < 1e-5);
        }
    }

    #[test]
    fn profile_and_arc_follow_the_axis() {
        let sw = Sweep::new(
            &[v3(0.0, 1.0, 0.0), v3(0.0, 0.0, 0.0)],
            &[V3::X, V3::X],
            &[
                key(0.0, 0.1, 0.1, 0.0, 0.0),
                key(1.0, 0.05, 0.05, 0.02, 0.0),
            ],
            1.0,
            (1.0, 1.0),
        );
        // narrower and shifted forward toward the bottom
        let (d_top, q) = sw.eval(v3(0.1, 0.99, 0.0));
        assert!(d_top.abs() < 0.01);
        assert!(q.y < 0.05, "arc starts at the first joint");
        let (d_bot, q) = sw.eval(v3(0.07, 0.01, 0.0));
        assert!(d_bot.abs() < 0.01, "bottom section {d_bot}");
        assert!(q.y > 0.9);
    }
}
