//! Timeline: sequences the shots. Given a time it returns the full scene state: the man's solved
//! skeleton, the chair's compression, the camera, and the grading/letterbox/fade values.
//! All times come from `timing.rs`; all poses from `poses.rs`.

use super::camera::{self, Camera};
use super::gait;
use super::math::{monotone_cubic, remap, v3, Ease, V3};
use super::poses as P;
use super::scene::{chair_pins, seat_top, ChairState, CHAIR_POS, STOP_OFFSET};
use super::skeleton::{solve, Joints, Pins, Pose, POSE_CHANNELS};
use super::timing as T;

/// Where he is when the walk window starts (just off-screen right), x in metres: he enters the
/// picture at about 0.3 s and walks about 2 m in it to his stop mark.
pub const WALK_FROM_X: f32 = 2.05;
/// Seat cushion compression on contact, metres.
pub const SEAT_SQUASH: f32 = 0.032;
/// Back cushion compression when he leans back, metres.
pub const BACK_SQUASH: f32 = 0.028;
/// Getting comfortable, as offsets added to the pose over [`T::COMFORT`]: at each fraction of
/// the window, (trunk tilt in degrees toward his right, pelvis shift toward his right in metres,
/// extra shoulder drop in metres). He eases his weight onto his right hip, rolls across to the
/// left one, and comes back to centre as his shoulders let go.
pub const COMFORT_KEYS: [(f32, f32, f32, f32); 5] = [
    (0.0, 0.0, 0.0, 0.0),
    (0.3, 2.2, 0.009, 0.0),
    (0.62, -1.3, -0.006, 0.008),
    (0.85, -0.3, -0.001, 0.004),
    (1.0, 0.0, 0.0, 0.0),
];

/// One frame's worth of animated values.
#[derive(Clone, Debug)]
pub struct FrameState {
    pub t: f32,
    pub joints: Joints,
    pub chair: ChairState,
    pub camera: Camera,
    /// 0..1 picture brightness (fade up from black at the start).
    pub exposure: f32,
    /// 0..1 how far the letterbox bars have slid in.
    pub letterbox: f32,
    /// 0..1 warm grade of the final hold.
    pub warmth: f32,
    /// 0..1 fade into the app's screen at the end.
    pub fade_out: f32,
    /// 0..1 how much handheld drift and breathing are active (for tests and "subtle" mode).
    pub life: f32,
}

/// A pose reached at time `t`.
#[derive(Clone, Copy)]
pub struct PoseKey {
    pub t: f32,
    pub pose: &'static Pose,
}

/// The sit-down: each beat's pose at the end of its window (see `timing.rs`).
pub fn sit_track() -> [PoseKey; 6] {
    [
        PoseKey {
            t: T::SIT_REACH.0,
            pose: &P::STAND,
        },
        PoseKey {
            t: T::SIT_REACH.1,
            pose: &P::HANDS_ON_ARMS,
        },
        PoseKey {
            t: T::SIT_LOWER.1,
            pose: &P::LOWERING,
        },
        PoseKey {
            t: T::SIT_LAND.1,
            pose: &P::SEATED_UPRIGHT,
        },
        PoseKey {
            t: T::SIT_SETTLE.1,
            pose: &P::SEATED_SETTLED,
        },
        PoseKey {
            t: T::RECLINE.1,
            pose: &P::SEATED_RELAXED,
        },
    ]
}

/// Pose at time `t` along `keys`: every pose value follows its own smooth monotone curve through
/// the keys (see [`monotone_cubic`]), so motion carries through a beat instead of stopping.
pub fn eval_track(keys: &[PoseKey], t: f32) -> Pose {
    let ts: Vec<f32> = keys.iter().map(|k| k.t).collect();
    let chans: Vec<[f32; POSE_CHANNELS]> = keys.iter().map(|k| k.pose.channels()).collect();
    let mut out = [0.0; POSE_CHANNELS];
    let mut vs = vec![0.0; keys.len()];
    for (c, o) in out.iter_mut().enumerate() {
        for (v, ch) in vs.iter_mut().zip(&chans) {
            *v = ch[c];
        }
        *o = monotone_cubic(&ts, &vs, t);
    }
    Pose::from_channels(&out)
}

/// Getting-comfortable offsets at `t`: (tilt degrees, pelvis shift metres, shoulder drop metres).
pub fn comfort(t: f32) -> (f32, f32, f32) {
    let ts: Vec<f32> = COMFORT_KEYS
        .iter()
        .map(|k| T::COMFORT.0 + k.0 * (T::COMFORT.1 - T::COMFORT.0))
        .collect();
    let ch = |f: fn(&(f32, f32, f32, f32)) -> f32| {
        let vs: Vec<f32> = COMFORT_KEYS.iter().map(f).collect();
        monotone_cubic(&ts, &vs, t)
    };
    (ch(|k| k.1), ch(|k| k.2), ch(|k| k.3))
}

/// Walk progress at time `t`: (fraction of the distance covered, speed as a fraction of cruise).
/// Full speed when he enters, a smooth cosine deceleration to a stop at the end.
pub fn walk_progress(t: f32) -> (f32, f32) {
    let a = T::WALK_CRUISE_FRACTION;
    let total = a + 0.5 * (1.0 - a);
    let u = (t - T::WALK.0) / (T::WALK.1 - T::WALK.0);
    if u <= 0.0 {
        return (u * 1.0 / total, 1.0); // still walking toward the frame edge before t = WALK.0
    }
    if u >= 1.0 {
        return (1.0, 0.0);
    }
    if u < a {
        return (u / total, 1.0);
    }
    let w = (u - a) / (1.0 - a);
    let pi = std::f32::consts::PI;
    let dist = a + 0.5 * (u - a) + 0.5 * (1.0 - a) / pi * (pi * w).sin();
    (dist / total, 0.5 * (1.0 + (pi * w).cos()))
}

/// Floor point between his feet where he stops, and turns, and sits.
pub fn stop_point() -> V3 {
    CHAIR_POS + v3(STOP_OFFSET, 0.0, 0.0)
}

/// Contact points: chair grips and rests, feet where STAND puts them at the stop point.
pub fn pins() -> Pins {
    let mut pins = chair_pins();
    pins.feet = gait::stand_feet(stop_point(), 0.0);
    pins
}

/// Animation options that change the content, not the timing.
#[derive(Clone, Copy, Debug)]
pub struct Style {
    /// Handheld drift and breathing in the hold.
    pub life: bool,
}

/// The whole scene at time `t` (seconds from launch).
pub fn frame_at(t: f32, style: Style) -> FrameState {
    let t = t.clamp(0.0, T::TOTAL);
    let pins = pins();
    let stop = stop_point();

    // ---- body: base position, facing, pose and foot contacts
    let mut pins = pins;
    let (yaw, base, mut pose) = if t < T::SIT_REACH.0 {
        let g = if t < T::TURN.0 {
            gait::walk(t, WALK_FROM_X, stop)
        } else {
            gait::turn(t, stop)
        };
        pins.feet = g.feet;
        pins.foot_frame = g.foot_frame;
        (g.yaw, g.base, g.pose)
    } else if t < T::TURN.1 {
        // the sit-down has begun while the feet and facing finish the turn (its pose is back in
        // STAND here, so the two meet without a jump); the turn's weight shift stays on
        let g = gait::turn(t, stop);
        pins.feet = g.feet;
        pins.foot_frame = g.foot_frame;
        let mut p = eval_track(&sit_track(), t);
        p.pelvis[1] += g.pose.pelvis[1] - P::STAND.pelvis[1];
        p.pelvis[2] += g.pose.pelvis[2] - P::STAND.pelvis[2];
        p.foot_pin = g.pose.foot_pin;
        (g.yaw, g.base, p)
    } else {
        (0.0, stop, eval_track(&sit_track(), t))
    };

    // getting comfortable
    let (tilt, shift, drop) = comfort(t);
    pose.tilt += tilt;
    pose.pelvis[2] += shift;
    pose.shoulder_drop += drop;

    // breathing and handheld drift in the final hold
    let hold_in = remap(t, T::HOLD.0, T::HOLD.0 + T::HANDHELD_RAMP);
    let life = if style.life { hold_in } else { 0.0 };
    if life > 0.0 {
        pose.shoulder_drop += 0.006 * (std::f32::consts::TAU * t / 3.2).sin() * life;
    }

    let joints = solve(&pose, base, yaw, &pins);

    // ---- chair
    let chair = ChairState {
        seat_squash: SEAT_SQUASH * Ease::OutBack.apply(remap(t, T::CUSHION.0, T::CUSHION.1)),
        back_squash: BACK_SQUASH
            * Ease::InOutCubic.apply(remap(t, T::SIT_SETTLE.0, T::SIT_SETTLE.1)),
    };

    // ---- camera
    let cam_pose = camera::eval_path(&camera::path(), t);
    let cam_pose = camera::handheld(&cam_pose, t, life);

    FrameState {
        t,
        joints,
        chair,
        camera: cam_pose.camera(),
        exposure: Ease::InOutSine.apply(remap(t, 0.0, T::FADE_IN_END)),
        letterbox: Ease::OutCubic.apply(remap(t, T::LETTERBOX_IN.0, T::LETTERBOX_IN.1)),
        warmth: Ease::InOutSine.apply(remap(t, T::HOLD.0, T::HOLD.1)),
        fade_out: Ease::InOutSine.apply(remap(t, T::FADE_OUT.0, T::FADE_OUT.1)),
        life,
    }
}

/// Seat top including compression (used by tests to check contact timing).
pub fn seat_surface(chair: &ChairState) -> f32 {
    seat_top() - chair.seat_squash
}

/// Lowest point of his pelvis box (bottom of the seat contact), for tests.
pub fn pelvis_bottom(j: &Joints) -> f32 {
    j.pelvis.y - 0.085
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Jolts are measured over this step (the limits were set for it), whatever the frame rate.
    const JOLT_DT: f32 = 1.0 / 30.0;

    const STYLE: Style = Style { life: true };

    #[test]
    fn walk_progress_is_monotonic_and_ends_still() {
        let mut prev = -1.0;
        let mut t = T::WALK.0;
        while t <= T::WALK.1 {
            let (d, v) = walk_progress(t);
            assert!(d >= prev - 1e-6, "walk goes backward at {t}");
            assert!((0.0..=1.0).contains(&v));
            prev = d;
            t += 0.01;
        }
        assert_eq!(walk_progress(T::WALK.1), (1.0, 0.0));
        assert_eq!(walk_progress(T::WALK.0).1, 1.0);
    }

    #[test]
    fn he_starts_off_screen_and_ends_seated() {
        // not in the picture while it fades up: every joint right of the opening frame's edge
        for t in [0.0, 0.1, 0.2] {
            let f = frame_at(t, STYLE);
            let (fwd, right, _) = f.camera.basis();
            let half_tan = (f.camera.fov.to_radians() / 2.0).tan() * 2.39; // horizontal, at 2.39:1
            let j = &f.joints;
            let mut pts = vec![j.pelvis, j.spine_mid, j.neck, j.head];
            for s in 0..2 {
                pts.extend([j.shoulder[s], j.elbow[s], j.hand[s], j.hip[s], j.knee[s], j.ankle[s], j.heel[s], j.toe[s]]);
            }
            for p in pts {
                let q = p - f.camera.pos;
                let u = q.dot(right) / q.dot(fwd) / half_tan;
                assert!(u > 1.0, "a joint is in the picture at {t}: u {u} at {p:?}");
            }
        }
        // ... and walks into it soon after
        let f = frame_at(T::FADE_IN_END, STYLE);
        let (fwd, right, _) = f.camera.basis();
        let q = f.joints.pelvis - f.camera.pos;
        assert!(q.dot(right) / q.dot(fwd) / ((f.camera.fov.to_radians() / 2.0).tan() * 2.39) < 1.0, "walked into the picture by the end of the fade-in");
        let end = frame_at(T::TOTAL, STYLE);
        let seat = seat_surface(&end.chair);
        let bottom = pelvis_bottom(&end.joints);
        assert!(
            (bottom - seat).abs() < 0.04,
            "pelvis {bottom} on seat {seat}"
        );
        // reclined, but still facing the camera side (+X) and looking forward-up (the hands are
        // animated in blender/scene.py: they hold the laptop)
        assert!(end.joints.head_frame.col(0).x > 0.4);
        assert!(end.joints.head.x < end.joints.pelvis.x, "head back toward the headrest");
    }

    #[test]
    fn hands_reach_grips_in_beat_a() {
        let f = frame_at(T::SIT_REACH.1, STYLE);
        let pins = pins();
        for s in 0..2 {
            assert!(
                (f.joints.hand[s] - pins.grip[s]).length() < 0.02,
                "hand {s} off its grip by {}", (f.joints.hand[s] - pins.grip[s]).length()
            );
        }
    }

    #[test]
    fn no_nans_and_no_jumps() {
        let n = T::total_frames();
        let mut prev: Option<FrameState> = None;
        for i in 0..=n {
            let f = frame_at(i as f32 / T::FPS, STYLE);
            for p in f.joints.all_points() {
                assert!(p.is_finite(), "NaN at frame {i}");
            }
            if let Some(pr) = prev {
                if f.t > T::WALK.0 {
                    let jump = (f.joints.head - pr.joints.head).length();
                    assert!(jump < 0.12, "head jumps {jump} m at frame {i}");
                }
            }
            prev = Some(f);
        }
    }

    #[test]
    fn sit_down_flows_without_jolts() {
        // joint accelerations through the sit-down stay human: no stop-start or shaking
        let dt = JOLT_DT;
        let pts = |t: f32| {
            let j = frame_at(t, STYLE).joints;
            [
                j.pelvis, j.head, j.hand[0], j.hand[1], j.elbow[0], j.elbow[1],
            ]
        };
        let mut t = T::SIT_REACH.0;
        while t < T::HOLD.0 {
            let (a, b, c) = (pts(t - dt), pts(t), pts(t + dt));
            for k in 0..a.len() {
                let acc = ((c[k] - b[k] * 2.0) + a[k]).length() / (dt * dt);
                // the body (pelvis, head) stays smooth; the hands are re-animated in Blender
                let limit = if k < 2 { 15.0 } else { 20.0 };
                assert!(acc < limit, "joint {k} accelerates {acc} m/s2 at {t}");
            }
            t += dt;
        }
    }

    #[test]
    fn walk_and_turn_are_smooth_and_grounded() {
        let dt = JOLT_DT;
        let j = |t: f32| frame_at(t, STYLE).joints;
        let mut t = T::WALK.0;
        while t < T::SIT_REACH.0 {
            let (a, b, c) = (j(t - dt), j(t), j(t + dt));
            // the body itself moves without jolts (feet and hands swing faster, as they do)
            for (pa, pb, pc) in [(a.pelvis, b.pelvis, c.pelvis), (a.head, b.head, c.head)] {
                let acc = ((pc - pb * 2.0) + pa).length() / (dt * dt);
                assert!(acc < 8.0, "body accelerates {acc} m/s2 at {t}");
            }
            // a foot resting flat on the floor doesn't slide
            for s in 0..2 {
                let flat = |x: &Joints| (x.toe[s].y - x.heel[s].y).abs() < 0.002;
                let down = |x: &Joints| x.ankle[s].y < 0.075;
                if flat(&b) && flat(&c) && down(&b) && down(&c) {
                    let slide = v3(
                        c.ankle[s].x - b.ankle[s].x,
                        0.0,
                        c.ankle[s].z - b.ankle[s].z,
                    );
                    assert!(slide.length() < 0.004, "foot {s} slides {slide:?} at {t}");
                }
            }
            t += dt;
        }
        // the walk ends exactly in the standing pose's footprint
        let end = j(T::TURN.0);
        let stand = gait::stand_feet(stop_point(), 180.0);
        for (s, home) in stand.iter().enumerate() {
            assert!((end.ankle[s] - *home).length() < 0.01, "foot {s} not home");
        }
    }

    #[test]
    fn comfort_shift_starts_and_ends_at_rest() {
        assert_eq!(comfort(T::COMFORT.0 - 0.1), (0.0, 0.0, 0.0));
        assert_eq!(comfort(T::COMFORT.1 + 0.1), (0.0, 0.0, 0.0));
        let (tilt, _, _) = comfort(T::COMFORT.0 + 0.3 * (T::COMFORT.1 - T::COMFORT.0));
        assert!(tilt > 1.0, "weight goes onto one hip");
    }

    #[test]
    fn subtle_style_has_no_handheld() {
        let a = frame_at(10.6, Style { life: false });
        let b = frame_at(10.6, STYLE);
        assert_eq!(a.life, 0.0);
        assert!(b.life > 0.9);
        assert!((a.camera.pos - b.camera.pos).length() > 0.0);
    }
}
