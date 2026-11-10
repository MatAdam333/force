//! Small linear algebra, easing and noise helpers. Everything is `f32`; world units are metres,
//! angles in the public pose/camera data are degrees and converted here.

use std::ops::{Add, AddAssign, Div, Mul, Neg, Sub};

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct V3 {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

pub const fn v3(x: f32, y: f32, z: f32) -> V3 {
    V3 { x, y, z }
}

impl V3 {
    pub const ZERO: V3 = v3(0.0, 0.0, 0.0);
    pub const X: V3 = v3(1.0, 0.0, 0.0);
    pub const Y: V3 = v3(0.0, 1.0, 0.0);
    pub const Z: V3 = v3(0.0, 0.0, 1.0);

    pub fn splat(s: f32) -> V3 {
        v3(s, s, s)
    }
    pub fn dot(self, o: V3) -> f32 {
        self.x * o.x + self.y * o.y + self.z * o.z
    }
    pub fn cross(self, o: V3) -> V3 {
        v3(
            self.y * o.z - self.z * o.y,
            self.z * o.x - self.x * o.z,
            self.x * o.y - self.y * o.x,
        )
    }
    pub fn length(self) -> f32 {
        self.dot(self).sqrt()
    }
    /// Unit vector; a zero vector stays zero instead of becoming NaN.
    pub fn normalize(self) -> V3 {
        let l = self.length();
        if l > 1e-12 {
            self * (1.0 / l)
        } else {
            V3::ZERO
        }
    }
    /// Component-wise product.
    pub fn hadamard(self, o: V3) -> V3 {
        v3(self.x * o.x, self.y * o.y, self.z * o.z)
    }
    pub fn abs(self) -> V3 {
        v3(self.x.abs(), self.y.abs(), self.z.abs())
    }
    pub fn max_s(self, s: f32) -> V3 {
        v3(self.x.max(s), self.y.max(s), self.z.max(s))
    }
    pub fn max_comp(self) -> f32 {
        self.x.max(self.y).max(self.z)
    }
    pub fn lerp(self, o: V3, t: f32) -> V3 {
        self + (o - self) * t
    }
    pub fn luma(self) -> f32 {
        0.2126 * self.x + 0.7152 * self.y + 0.0722 * self.z
    }
    pub fn is_finite(self) -> bool {
        self.x.is_finite() && self.y.is_finite() && self.z.is_finite()
    }
}

impl Add for V3 {
    type Output = V3;
    fn add(self, o: V3) -> V3 {
        v3(self.x + o.x, self.y + o.y, self.z + o.z)
    }
}
impl AddAssign for V3 {
    fn add_assign(&mut self, o: V3) {
        *self = *self + o;
    }
}
impl Sub for V3 {
    type Output = V3;
    fn sub(self, o: V3) -> V3 {
        v3(self.x - o.x, self.y - o.y, self.z - o.z)
    }
}
impl Mul<f32> for V3 {
    type Output = V3;
    fn mul(self, s: f32) -> V3 {
        v3(self.x * s, self.y * s, self.z * s)
    }
}
impl Div<f32> for V3 {
    type Output = V3;
    fn div(self, s: f32) -> V3 {
        v3(self.x / s, self.y / s, self.z / s)
    }
}
impl Neg for V3 {
    type Output = V3;
    fn neg(self) -> V3 {
        v3(-self.x, -self.y, -self.z)
    }
}

/// Row-major 3x3 matrix; columns are the images of the basis vectors.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct M3 {
    pub r: [[f32; 3]; 3],
}

impl M3 {
    pub const I: M3 = M3 {
        r: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    };

    /// Rotation about +X by `a` radians (right-handed).
    pub fn rot_x(a: f32) -> M3 {
        let (s, c) = a.sin_cos();
        M3 {
            r: [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]],
        }
    }
    /// Rotation about +Y by `a` radians.
    pub fn rot_y(a: f32) -> M3 {
        let (s, c) = a.sin_cos();
        M3 {
            r: [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]],
        }
    }
    /// Rotation about +Z by `a` radians.
    pub fn rot_z(a: f32) -> M3 {
        let (s, c) = a.sin_cos();
        M3 {
            r: [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]],
        }
    }
    /// Matrix whose columns are `x`, `y`, `z`.
    pub fn from_cols(x: V3, y: V3, z: V3) -> M3 {
        M3 {
            r: [[x.x, y.x, z.x], [x.y, y.y, z.y], [x.z, y.z, z.z]],
        }
    }
    pub fn col(&self, i: usize) -> V3 {
        v3(self.r[0][i], self.r[1][i], self.r[2][i])
    }
    pub fn transpose(&self) -> M3 {
        let r = &self.r;
        M3 {
            r: [
                [r[0][0], r[1][0], r[2][0]],
                [r[0][1], r[1][1], r[2][1]],
                [r[0][2], r[1][2], r[2][2]],
            ],
        }
    }
}

impl Mul<V3> for M3 {
    type Output = V3;
    fn mul(self, v: V3) -> V3 {
        let r = &self.r;
        v3(
            r[0][0] * v.x + r[0][1] * v.y + r[0][2] * v.z,
            r[1][0] * v.x + r[1][1] * v.y + r[1][2] * v.z,
            r[2][0] * v.x + r[2][1] * v.y + r[2][2] * v.z,
        )
    }
}

impl Mul<M3> for M3 {
    type Output = M3;
    fn mul(self, o: M3) -> M3 {
        let mut out = [[0.0f32; 3]; 3];
        for (i, row) in out.iter_mut().enumerate() {
            for (j, cell) in row.iter_mut().enumerate() {
                *cell = (0..3).map(|k| self.r[i][k] * o.r[k][j]).sum();
            }
        }
        M3 { r: out }
    }
}

pub fn rad(deg: f32) -> f32 {
    deg * std::f32::consts::PI / 180.0
}

pub fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t
}

pub fn clamp01(t: f32) -> f32 {
    if t.is_nan() {
        0.0
    } else {
        t.clamp(0.0, 1.0)
    }
}

/// Position of `t` inside `[a, b]`, clamped to 0..1. A zero-length window is a step at `a`.
pub fn remap(t: f32, a: f32, b: f32) -> f32 {
    if b <= a {
        return if t >= a { 1.0 } else { 0.0 };
    }
    clamp01((t - a) / (b - a))
}

pub fn smoothstep(e0: f32, e1: f32, x: f32) -> f32 {
    let t = remap(x, e0, e1);
    t * t * (3.0 - 2.0 * t)
}

/// Easing curves. Every curve maps 0 -> 0 and 1 -> 1; input is clamped first.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ease {
    Linear,
    InOutCubic,
    InOutQuint,
    OutCubic,
    InCubic,
    /// Ease-in-out sine: the gentlest S-curve, for slow drifts.
    InOutSine,
    /// Overshoots slightly past 1 before settling (cushion bounce).
    OutBack,
}

impl Ease {
    pub fn apply(self, t: f32) -> f32 {
        let t = clamp01(t);
        match self {
            Ease::Linear => t,
            Ease::InOutCubic => {
                if t < 0.5 {
                    4.0 * t * t * t
                } else {
                    1.0 - (-2.0 * t + 2.0).powi(3) / 2.0
                }
            }
            Ease::InOutQuint => {
                if t < 0.5 {
                    16.0 * t.powi(5)
                } else {
                    1.0 - (-2.0 * t + 2.0).powi(5) / 2.0
                }
            }
            Ease::OutCubic => 1.0 - (1.0 - t).powi(3),
            Ease::InCubic => t * t * t,
            Ease::InOutSine => -((std::f32::consts::PI * t).cos() - 1.0) / 2.0,
            Ease::OutBack => {
                let c1 = 1.70158;
                let c3 = c1 + 1.0;
                1.0 + c3 * (t - 1.0).powi(3) + c1 * (t - 1.0).powi(2)
            }
        }
    }
}

/// Smooth curve through keyframes `(ts[k], vs[k])`: piecewise cubic Hermite with monotone
/// (Fritsch-Butland) tangents. It passes through every key, never overshoots between them, and
/// its velocity is continuous, so motion flows through a key instead of stopping there; it only
/// comes to rest at the first and last key and where a value turns around. Clamped outside.
pub fn monotone_cubic(ts: &[f32], vs: &[f32], t: f32) -> f32 {
    let n = ts.len().min(vs.len());
    if n == 0 {
        return 0.0;
    }
    if n == 1 || t <= ts[0] {
        return vs[0];
    }
    if t >= ts[n - 1] {
        return vs[n - 1];
    }
    let k = (1..n).find(|&k| t <= ts[k]).unwrap_or(n - 1) - 1;
    let slope = |i: usize| {
        let h = ts[i + 1] - ts[i];
        if h > 1e-6 {
            (vs[i + 1] - vs[i]) / h
        } else {
            0.0
        }
    };
    let tangent = |i: usize| {
        if i == 0 || i == n - 1 {
            return 0.0; // starts and ends at rest
        }
        let (d0, d1) = (slope(i - 1), slope(i));
        if d0 * d1 <= 0.0 {
            return 0.0;
        }
        let (h0, h1) = (ts[i] - ts[i - 1], ts[i + 1] - ts[i]);
        let (w0, w1) = (2.0 * h1 + h0, h1 + 2.0 * h0);
        (w0 + w1) / (w0 / d0 + w1 / d1)
    };
    let h = ts[k + 1] - ts[k];
    if h <= 1e-6 {
        return vs[k + 1];
    }
    let u = (t - ts[k]) / h;
    let (u2, u3) = (u * u, u * u * u);
    let h00 = 2.0 * u3 - 3.0 * u2 + 1.0;
    let h10 = u3 - 2.0 * u2 + u;
    let h01 = -2.0 * u3 + 3.0 * u2;
    let h11 = u3 - u2;
    h00 * vs[k] + h10 * h * tangent(k) + h01 * vs[k + 1] + h11 * h * tangent(k + 1)
}

/// Uniform Catmull-Rom spline segment from `p1` (t = 0) to `p2` (t = 1); continuous velocity
/// across consecutive segments.
pub fn catmull_rom(p0: f32, p1: f32, p2: f32, p3: f32, t: f32) -> f32 {
    let t2 = t * t;
    let t3 = t2 * t;
    0.5 * (2.0 * p1
        + (p2 - p0) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (3.0 * p1 - p0 - 3.0 * p2 + p3) * t3)
}

/// Smooth 3D value noise in `[-1, 1]` (quintic fade, hashed lattice).
pub fn noise3(p: V3) -> f32 {
    let (fx, fy, fz) = (p.x.floor(), p.y.floor(), p.z.floor());
    let (ix, iy, iz) = (fx as i32, fy as i32, fz as i32);
    let fade = |t: f32| t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
    let (ux, uy, uz) = (fade(p.x - fx), fade(p.y - fy), fade(p.z - fz));
    let h = |x: i32, y: i32, z: i32| hash01(x as u32, y as u32, z as u32 ^ 0x5bd1_e995);
    let lx = |y: i32, z: i32| lerp(h(ix, y, z), h(ix + 1, y, z), ux);
    let ly = |z: i32| lerp(lx(iy, z), lx(iy + 1, z), uy);
    lerp(ly(iz), ly(iz + 1), uz) * 2.0 - 1.0
}

/// Two octaves of [`noise3`], roughly in `[-1, 1]`.
pub fn fbm2(p: V3) -> f32 {
    noise3(p) * 0.67 + noise3(p * 2.03 + v3(17.1, 3.7, 9.2)) * 0.33
}

/// Deterministic hash of two integers and a seed to `[0, 1)`. Used for grain and dither.
pub fn hash01(x: u32, y: u32, seed: u32) -> f32 {
    let mut h =
        x.wrapping_mul(0x8da6_b343) ^ y.wrapping_mul(0xd816_3841) ^ seed.wrapping_mul(0xcb1a_b31f);
    h ^= h >> 15;
    h = h.wrapping_mul(0x2c1b_3c6d);
    h ^= h >> 12;
    h = h.wrapping_mul(0x297a_2d39);
    h ^= h >> 15;
    (h >> 8) as f32 / (1u32 << 24) as f32
}

/// 4x4 Bayer matrix threshold in `[0, 1)` for ordered dithering.
pub fn bayer4(x: usize, y: usize) -> f32 {
    const M: [[u8; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
    (M[y & 3][x & 3] as f32 + 0.5) / 16.0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: V3, b: V3) -> bool {
        (a - b).length() < 1e-5
    }

    #[test]
    fn monotone_cubic_hits_keys_without_overshoot() {
        let ts = [0.0, 1.0, 1.5, 3.0];
        let vs = [0.0, 10.0, 10.5, 2.0];
        for (t, v) in ts.iter().zip(vs) {
            assert!((monotone_cubic(&ts, &vs, *t) - v).abs() < 1e-5);
        }
        let mut prev = monotone_cubic(&ts, &vs, 0.0);
        let mut t = 0.0;
        while t <= 3.0 {
            let v = monotone_cubic(&ts, &vs, t);
            assert!((-1e-4..=10.5 + 1e-4).contains(&v), "overshoot {v} at {t}");
            assert!((v - prev).abs() < 0.2, "jump at {t}");
            // rising keys stay rising: no wobble between them
            if t < 1.5 {
                assert!(v >= prev - 1e-5, "not monotone at {t}");
            }
            prev = v;
            t += 0.01;
        }
        // velocity carries through a key on a monotone run (no dead stop at t = 1)
        let d = monotone_cubic(&ts, &vs, 1.01) - monotone_cubic(&ts, &vs, 0.99);
        assert!(d > 0.01);
        assert_eq!(monotone_cubic(&ts, &vs, -1.0), 0.0);
        assert_eq!(monotone_cubic(&ts, &vs, 9.0), 2.0);
    }

    #[test]
    fn noise_is_bounded_and_smooth() {
        let mut prev = noise3(V3::ZERO);
        for i in 0..2000 {
            let p = v3(i as f32 * 0.013, i as f32 * 0.007, -(i as f32) * 0.011);
            let n = noise3(p);
            assert!((-1.0..=1.0).contains(&n));
            assert!((n - prev).abs() < 0.12, "noise jumps at {p:?}");
            prev = n;
        }
    }

    #[test]
    fn rotations_are_right_handed() {
        let q = std::f32::consts::FRAC_PI_2;
        assert!(close(M3::rot_z(q) * V3::X, V3::Y));
        assert!(close(M3::rot_x(q) * V3::Y, V3::Z));
        assert!(close(M3::rot_y(q) * V3::Z, V3::X));
        // composition matches applying one after the other
        let a = M3::rot_y(0.3) * M3::rot_z(-0.7);
        let v = v3(0.2, -1.0, 0.4);
        assert!(close(a * v, M3::rot_y(0.3) * (M3::rot_z(-0.7) * v)));
        assert!(close(a.transpose() * (a * v), v));
    }

    #[test]
    fn easing_endpoints_and_clamping() {
        for e in [
            Ease::Linear,
            Ease::InOutCubic,
            Ease::InOutQuint,
            Ease::OutCubic,
            Ease::InCubic,
            Ease::InOutSine,
            Ease::OutBack,
        ] {
            assert!(e.apply(0.0).abs() < 1e-6, "{e:?}");
            assert!((e.apply(1.0) - 1.0).abs() < 1e-6, "{e:?}");
            assert_eq!(e.apply(-3.0), e.apply(0.0));
            assert_eq!(e.apply(f32::NAN), e.apply(0.0));
            assert_eq!(e.apply(7.0), e.apply(1.0));
        }
        assert!(Ease::OutBack.apply(0.8) > 1.0, "OutBack overshoots");
        assert!((Ease::InOutQuint.apply(0.5) - 0.5).abs() < 1e-6);
    }

    #[test]
    fn remap_handles_degenerate_windows() {
        assert_eq!(remap(0.5, 1.0, 1.0), 0.0);
        assert_eq!(remap(1.0, 1.0, 1.0), 1.0);
        assert_eq!(remap(2.0, 1.0, 3.0), 0.5);
        assert_eq!(remap(f32::NAN, 0.0, 1.0), 0.0);
    }

    #[test]
    fn normalize_zero_is_zero() {
        assert_eq!(V3::ZERO.normalize(), V3::ZERO);
    }

    #[test]
    fn hash_is_in_range_and_deterministic() {
        for i in 0..1000 {
            let h = hash01(i, i * 7, 3);
            assert!((0.0..1.0).contains(&h));
            assert_eq!(h, hash01(i, i * 7, 3));
        }
    }
}
