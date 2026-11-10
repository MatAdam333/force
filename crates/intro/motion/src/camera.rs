//! Camera: position, look-at target and vertical field of view, animated along a keyed path.
//! Keys are orbit-style (target + azimuth/elevation/distance) so interpolating between a side
//! and a front view sweeps a real arc around the subject instead of cutting through it.

use super::math::{lerp, rad, remap, v3, Ease, V3};
use super::scene::CHAIR_POS;
use super::timing as T;

/// Where the camera is, as an orbit around `target`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CamPose {
    pub target: V3,
    /// Degrees around +Y: 0 = camera on +Z looking along -Z (side view, chair on the left);
    /// 90 = camera on +X looking straight at the chair's front (the front framings sit ~13 degrees
    /// off-axis so the armrest tops and forearms read).
    pub azimuth: f32,
    /// Degrees above the target.
    pub elevation: f32,
    /// Metres from the target.
    pub distance: f32,
    /// Vertical field of view of the letterboxed picture, degrees.
    pub fov: f32,
}

impl CamPose {
    pub fn lerp(&self, o: &CamPose, t: f32) -> CamPose {
        CamPose {
            target: self.target.lerp(o.target, t),
            azimuth: lerp(self.azimuth, o.azimuth, t),
            elevation: lerp(self.elevation, o.elevation, t),
            distance: lerp(self.distance, o.distance, t),
            fov: lerp(self.fov, o.fov, t),
        }
    }

    pub fn camera(&self) -> Camera {
        let (az, el) = (rad(self.azimuth), rad(self.elevation));
        let offset = v3(az.sin() * el.cos(), el.sin(), az.cos() * el.cos()) * self.distance;
        Camera {
            pos: self.target + offset,
            target: self.target,
            fov: self.fov,
        }
    }
}

/// A resolved camera for one frame.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Camera {
    pub pos: V3,
    pub target: V3,
    pub fov: f32,
}

impl Camera {
    /// (forward, right, up) unit vectors.
    pub fn basis(&self) -> (V3, V3, V3) {
        let fwd = (self.target - self.pos).normalize();
        let right = fwd.cross(V3::Y).normalize();
        let up = right.cross(fwd);
        (fwd, right, up)
    }
}

/// The camera reaches `pose` at time `t`, easing from the previous key with `ease`.
#[derive(Clone, Copy, Debug)]
pub struct CamKey {
    pub t: f32,
    pub pose: CamPose,
    pub ease: Ease,
}

// ---- Framings -------------------------------------------------------------------------------
/// Side view: the chair left of centre, his stop mark right of it, and the frame's right edge
/// about 2 m from the mark (he walks in from just beyond it). About 4.2 m wide at his walk, so a
/// 16:9 crop still holds him head to toe with room to spare.
pub const SIDE_WIDE: CamPose = CamPose {
    target: v3(-0.95, 0.92, 0.0),
    azimuth: -10.0,
    elevation: 4.0,
    distance: 2.75,
    fov: 36.0,
};
/// End of the slow drift toward the chair.
pub const SIDE_DRIFT: CamPose = CamPose {
    target: v3(-1.05, 0.9, 0.0),
    azimuth: -8.0,
    elevation: 4.5,
    distance: 2.62,
    fov: 36.0,
};
/// Front view at the end of the orbit: his whole standing figure and the chair.
pub const FRONT: CamPose = CamPose {
    target: v3(CHAIR_POS.x + 0.15, 0.92, 0.0),
    azimuth: 78.0,
    elevation: 9.0,
    distance: 3.4,
    fov: 32.0,
};
/// Pushed in and tilted down with him as he sits: chair and man fill the frame.
pub const FRONT_SAT: CamPose = CamPose {
    target: v3(CHAIR_POS.x + 0.05, 0.67, 0.0),
    azimuth: 76.0,
    elevation: 15.0,
    distance: 2.6,
    fov: 31.0,
};
/// A touch closer by the end of the sit-down.
pub const FRONT_SETTLED: CamPose = CamPose {
    target: v3(CHAIR_POS.x + 0.04, 0.66, 0.0),
    azimuth: 76.0,
    elevation: 15.0,
    distance: 2.5,
    fov: 31.0,
};
/// End of the slow push-in on the final pose.
pub const HOLD_END: CamPose = CamPose {
    target: v3(CHAIR_POS.x + 0.02, 0.66, 0.0),
    azimuth: 77.0,
    elevation: 13.5,
    distance: 2.25,
    fov: 30.0,
};

pub fn path() -> [CamKey; 7] {
    [
        CamKey {
            t: 0.0,
            pose: SIDE_WIDE,
            ease: Ease::Linear,
        },
        CamKey {
            t: T::DRIFT.0,
            pose: SIDE_WIDE,
            ease: Ease::Linear,
        },
        CamKey {
            t: T::DRIFT.1,
            pose: SIDE_DRIFT,
            ease: Ease::InOutSine,
        },
        CamKey {
            t: T::ORBIT.1,
            pose: FRONT,
            ease: Ease::InOutCubic,
        },
        CamKey {
            t: T::SIT_LAND.1,
            pose: FRONT_SAT,
            ease: Ease::InOutSine,
        },
        CamKey {
            t: T::HOLD.0,
            pose: FRONT_SETTLED,
            ease: Ease::InOutSine,
        },
        CamKey {
            t: T::HOLD.1,
            pose: HOLD_END,
            ease: Ease::InOutSine,
        },
    ]
}

/// Camera pose at time `t` along `keys` (sorted by time; clamps outside the range).
pub fn eval_path(keys: &[CamKey], t: f32) -> CamPose {
    let first = keys.first().expect("camera path has keys");
    if t <= first.t {
        return first.pose;
    }
    for w in keys.windows(2) {
        let (a, b) = (&w[0], &w[1]);
        if t <= b.t {
            return a.pose.lerp(&b.pose, b.ease.apply(remap(t, a.t, b.t)));
        }
    }
    keys.last().unwrap().pose
}

/// Small handheld wobble (sum of incommensurate sines), scaled by `amount` 0..1.
pub fn handheld(pose: &CamPose, t: f32, amount: f32) -> CamPose {
    if amount <= 0.0 {
        return *pose;
    }
    let n = |f: f32, ph: f32| (t * f + ph).sin();
    let dx = 0.006 * (n(1.3, 0.0) + 0.5 * n(2.9, 1.7));
    let dy = 0.005 * (n(1.1, 2.1) + 0.5 * n(3.3, 0.4));
    let daz = 0.35 * (n(0.7, 0.9) + 0.4 * n(1.9, 2.6));
    let del = 0.2 * (n(0.9, 1.3) + 0.4 * n(2.3, 0.2));
    CamPose {
        target: pose.target + v3(0.0, dy, dx) * amount,
        azimuth: pose.azimuth + daz * amount,
        elevation: pose.elevation + del * amount,
        ..*pose
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_is_continuous_and_orbits() {
        let keys = path();
        let mut prev = eval_path(&keys, 0.0).camera().pos;
        let mut t = 0.0;
        while t <= T::TOTAL {
            let c = eval_path(&keys, t).camera();
            assert!(
                (c.pos - prev).length() < 0.3,
                "camera jumps {} m at {t}",
                (c.pos - prev).length()
            );
            prev = c.pos;
            t += 1.0 / T::FPS;
        }
        let mid = eval_path(&keys, (T::ORBIT.0 + T::ORBIT.1) / 2.0);
        assert!(
            mid.azimuth > 20.0 && mid.azimuth < 70.0,
            "orbit midway: {}",
            mid.azimuth
        );
        assert!(
            eval_path(&keys, T::ORBIT.1).azimuth - eval_path(&keys, T::ORBIT.0).azimuth > 80.0,
            "~90 degree orbit"
        );
        // the orbit keeps its distance from the target: it sweeps, it does not cut through
        assert!(mid.distance > 2.4);
    }

    #[test]
    fn basis_is_orthonormal() {
        let c = FRONT.camera();
        let (f, r, u) = c.basis();
        assert!(f.dot(r).abs() < 1e-5 && f.dot(u).abs() < 1e-5 && r.dot(u).abs() < 1e-5);
        assert!(u.y > 0.9, "camera stays level");
        assert!(f.x < -0.85, "front view looks along -X");
    }
}
