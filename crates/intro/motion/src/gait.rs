//! Footstep gait for the walk-in and the turn. Feet are planted on the floor and stay put while
//! they carry weight; the legs reach them by IK, so knees bend and straighten on their own.
//!
//! Walk, per foot: stance (heel strike, roll onto the flat foot, heel rises, push off the ball
//! of the foot), then swing (the foot travels in an arc to its next step, toes coming up for
//! the heel strike). The pelvis bobs twice per stride and sways over the standing foot; the
//! upper body follows the named walk poses on a periodic spline. The last step closes beside
//! the other foot, so the walk ends exactly in his standing position.
//!
//! Turn: three small steps around his stop mark instead of swivelling on the spot.

use super::math::{catmull_rom, rad, remap, smoothstep, v3, Ease, M3, V3};
use super::poses as P;
use super::skeleton::{solve, Pins, Pose, POSE_CHANNELS};
use super::timing as T;

/// Distance covered by one full cycle (two steps), metres.
pub const STRIDE: f32 = 1.45;
/// Fraction of a cycle each foot spends on the ground.
const STANCE: f32 = 0.6;
/// Toes up at heel strike / heel up at push-off, degrees.
const HEEL_STRIKE: f32 = 14.0;
const TOE_OFF: f32 = 34.0;
/// Ankle lift at mid swing, metres.
const SWING_LIFT: f32 = 0.075;
/// Pelvis bob (up at mid stance, down at heel strike) and sway over the standing foot, metres.
const BOB: f32 = 0.013;
const SWAY: f32 = 0.02;
/// Pelvis height while walking at full speed (knees never lock).
const WALK_HEIGHT: f32 = 0.965;
/// Ankle lift of a turning step, metres.
const TURN_LIFT: f32 = 0.05;
/// The turn's upper-body bump is over at this fraction of the turn: from there the body is in
/// STAND again and the sit-down can begin while the feet and facing finish the turn.
/// ... and his facing has come round at this fraction (the last step lands a little later).
pub const TURN_YAW_END: f32 = 0.8;
pub const TURN_POSE_END: f32 = 0.8;
/// Distance from the ankle forward to the ball of the foot (push-off pivot), metres.
const BALL: f32 = 0.13;

/// One frame of body placement and contacts.
#[derive(Clone, Copy, Debug)]
pub struct Gait {
    /// Floor point the pose's pelvis offsets are measured from.
    pub base: V3,
    /// Body facing, degrees (0 = +X).
    pub yaw: f32,
    pub pose: Pose,
    /// Ankle targets and foot orientations (world).
    pub feet: [V3; 2],
    pub foot_frame: [M3; 2],
}

/// Where his feet stand at `stop` facing `yaw`: the standing pose's legs, straight, with the
/// pelvis at full hip height (the standing pose itself then rests on them with soft knees).
pub fn stand_feet(stop: V3, yaw: f32) -> [V3; 2] {
    let mut reference = P::STAND;
    reference.pelvis[1] = super::skeleton::HIP_HEIGHT;
    reference.foot_pin = 0.0;
    solve(&reference, stop, yaw, &Pins::none()).ankle
}

/// Foot frame facing `fwd` (horizontal), pitched by `pitch` degrees (+ toes up).
fn foot_frame(fwd: V3, pitch: f32) -> M3 {
    let (s, c) = rad(pitch).sin_cos();
    let f = fwd * c + V3::Y * s;
    let u = V3::Y * c - fwd * s;
    M3::from_cols(f, u, f.cross(u))
}

/// Ankle of a foot whose flat position is `flat`, rolled by `pitch` degrees about its heel
/// (pitch > 0, toes up) or the ball of the foot (pitch < 0, heel up).
fn rolled_ankle(flat: V3, fwd: V3, pitch: f32) -> V3 {
    let ground = v3(0.0, flat.y, 0.0);
    let pivot = if pitch >= 0.0 {
        flat - fwd * super::skeleton::HEEL - ground
    } else {
        flat + fwd * BALL - ground
    };
    let r = flat - pivot;
    let (a, b) = (r.dot(fwd), r.y);
    let fr = foot_frame(fwd, pitch);
    pivot + fr.col(0) * a + fr.col(1) * b
}

/// Foot pitch through stance (u 0..1): heel strike, flat, then the heel rises toward push-off.
fn stance_pitch(u: f32) -> f32 {
    if u < 0.15 {
        HEEL_STRIKE * (1.0 - smoothstep(0.0, 0.15, u))
    } else if u < 0.55 {
        0.0
    } else {
        let k = (u - 0.55) / 0.45;
        -TOE_OFF * k * k
    }
}

/// Ankle and pitch of one foot moving from flat position `from` to `to` over swing fraction `u`.
fn swing(from: V3, to: V3, fwd: V3, u: f32, lift: f32) -> (V3, f32) {
    let start = rolled_ankle(from, fwd, -TOE_OFF);
    let end = rolled_ankle(to, fwd, HEEL_STRIKE);
    // cubic with some speed already at toe-off (the foot is rolling off the ball) and a little
    // left at heel strike (the heel rocker takes it from there)
    let (m0, m1) = (0.45, 0.2);
    let u = u.clamp(0.0, 1.0);
    let h = (m0 + m1 - 2.0) * u * u * u + (3.0 - 2.0 * m0 - m1) * u * u + m0 * u;
    let arc = (std::f32::consts::PI * u).sin();
    let pitch = -TOE_OFF + (TOE_OFF + HEEL_STRIKE) * smoothstep(0.08, 0.92, u);
    (start.lerp(end, h) + V3::Y * (lift * arc), pitch)
}

/// Walk distance at time `t` (metres from where he is when the walk window starts; negative
/// before) and speed as a fraction of cruise.
fn walk_distance(t: f32, walk_len: f32) -> (f32, f32) {
    let (frac, speed) = super::timeline::walk_progress(t);
    (walk_len * frac, speed)
}

/// Upper body for the walk at cycle phase `psi` (0 = left heel strike): a periodic Catmull-Rom
/// spline through WALK_A, WALK_PASS_A, WALK_B, WALK_PASS_B, so no joint ever stops mid-stride.
pub fn walk_upper(psi: f32) -> Pose {
    let keys = [
        P::WALK_A.channels(),
        P::WALK_PASS_A.channels(),
        P::WALK_B.channels(),
        P::WALK_PASS_B.channels(),
    ];
    let x = psi.rem_euclid(1.0) * 4.0;
    let i = (x.floor() as usize).min(3);
    let f = x - i as f32;
    let k = |d: usize| &keys[(i + d) % 4];
    let mut out = [0.0; POSE_CHANNELS];
    for (c, o) in out.iter_mut().enumerate() {
        *o = catmull_rom(k(3)[c], k(0)[c], k(1)[c], k(2)[c], f);
    }
    Pose::from_channels(&out)
}

/// The walk-in at time `t`, ending at `stop` (facing -X, yaw 180).
pub fn walk(t: f32, from_x: f32, stop: V3) -> Gait {
    let walk_len = from_x - stop.x;
    let (s, speed) = walk_distance(t, walk_len);
    let s_end = walk_len;
    let fwd = -V3::X;
    let stand = stand_feet(stop, 180.0);
    // flat ankle position of a foot planted at walk distance p
    let flat = |side: usize, p: f32| stand[side] + V3::X * (s_end - p);
    let l = STRIDE * 0.5;
    let mut feet = [V3::ZERO; 2];
    let mut frames = [M3::I; 2];
    // his right foot (1) makes the last full step; the left (0) closes beside it
    for side in 0..2 {
        let last = if side == 1 { s_end } else { s_end - l };
        let m = (s - last + STANCE * 0.5 * STRIDE) / STRIDE;
        let j = m.floor();
        let frac = m - j;
        let plant = last + j * STRIDE;
        let (ankle, pitch) = if side == 0 && plant >= last && frac >= STANCE {
            // closing step: from the last plant to beside the right foot, done before he stops
            let u = remap(s, s_end - 0.2 * STRIDE, s_end - 0.03 * STRIDE);
            let (a, p) = swing(flat(0, plant), flat(0, s_end), fwd, u, SWING_LIFT * 0.6);
            let settle = smoothstep(0.85, 1.0, u);
            (a.lerp(flat(0, s_end), settle), p * (1.0 - settle))
        } else if frac < STANCE {
            let pitch = stance_pitch(frac / STANCE);
            (rolled_ankle(flat(side, plant), fwd, pitch), pitch)
        } else {
            let u = (frac - STANCE) / (1.0 - STANCE);
            swing(
                flat(side, plant),
                flat(side, plant + STRIDE),
                fwd,
                u,
                SWING_LIFT,
            )
        };
        feet[side] = ankle;
        frames[side] = foot_frame(fwd, pitch);
    }

    // phase 0 = left heel strike (WALK_A); the right foot is at mid stance at s = s_end
    let psi = (s - (s_end - l - STANCE * 0.5 * STRIDE)) / STRIDE;
    let k = smoothstep(0.0, 0.6, speed);
    let mut pose = P::STAND.lerp(&walk_upper(psi), k);
    let right_mid = (s - s_end) / STRIDE * std::f32::consts::TAU;
    pose.pelvis = [
        0.0,
        P::STAND.pelvis[1]
            + (WALK_HEIGHT - P::STAND.pelvis[1]) * k
            + BOB * k * (2.0 * right_mid).cos(),
        SWAY * k * right_mid.cos(),
    ];
    pose.foot_pin = 1.0;
    Gait {
        base: v3(from_x - s, 0.0, 0.0),
        yaw: 180.0,
        pose,
        feet,
        foot_frame: frames,
    }
}

/// The turn at time `t`: from facing -X (yaw 180) to facing the camera (+X, yaw 360) in three
/// small steps around the stop mark.
pub fn turn(t: f32, stop: V3) -> Gait {
    let u = remap(t, T::TURN.0, T::TURN.1);
    // his body faces the camera a little before the last step is set down
    let yaw = 180.0 + 180.0 * Ease::InOutSine.apply((u / TURN_YAW_END).min(1.0));
    let local = stand_feet(stop, 0.0).map(|a| a - stop);
    // (foot, window within the turn, yaw before, yaw after)
    const STEPS: [(usize, (f32, f32), f32, f32); 3] = [
        (1, (0.02, 0.4), 180.0, 270.0),
        (0, (0.3, 0.72), 180.0, 360.0),
        (1, (0.62, 0.96), 270.0, 360.0),
    ];
    let mut foot_yaw = [180.0f32; 2];
    let mut lift = [0.0f32; 2];
    for (side, (a, b), y0, y1) in STEPS {
        if u >= a {
            let v = remap(u, a, b);
            // the foot comes up first, then travels, and is set down before the weight goes on
            foot_yaw[side] = y0 + (y1 - y0) * smoothstep(0.15, 0.9, v);
            if v < 1.0 {
                // sin^2: the foot leaves and meets the floor gently
                lift[side] = TURN_LIFT * (std::f32::consts::PI * v).sin().powi(2);
            }
        }
    }
    let mut feet = [V3::ZERO; 2];
    let mut frames = [M3::I; 2];
    for side in 0..2 {
        let r = M3::rot_y(rad(foot_yaw[side]));
        feet[side] = stop + r * local[side] + V3::Y * lift[side];
        frames[side] = foot_frame(r * V3::X, 0.0);
    }
    // weight shifts onto the standing foot while the other one steps
    // (a hump with no acceleration at either end: the sit-down takes over from it smoothly)
    let v = (u / TURN_POSE_END).min(1.0);
    let bump = 64.0 * (v * (1.0 - v)).powi(3);
    let mut pose = P::STAND.lerp(&P::TURN, bump);
    let shift = (lift[0] - lift[1]) / TURN_LIFT;
    pose.pelvis[2] = 0.018 * shift;
    pose.pelvis[1] -= 0.012 * shift * shift;
    pose.foot_pin = 1.0;
    Gait {
        base: stop,
        yaw,
        pose,
        feet,
        foot_frame: frames,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rolling_keeps_the_pivot_on_the_floor() {
        let flat = v3(0.0, 0.07, 0.0);
        // no roll: the ankle stays put
        assert!((rolled_ankle(flat, V3::X, 0.0) - flat).length() < 1e-6);
        // heel up about the ball: the ankle rises and moves forward a little
        let up = rolled_ankle(flat, V3::X, -30.0);
        assert!(up.y > 0.12 && up.x > 0.0);
        // toes up about the heel: the ankle rises slightly
        assert!(rolled_ankle(flat, V3::X, 14.0).y > flat.y);
    }

    #[test]
    fn swing_starts_and_ends_on_the_contacts() {
        let (a, b) = (v3(0.0, 0.07, 0.1), v3(-1.45, 0.07, 0.1));
        let (s0, p0) = swing(a, b, -V3::X, 0.0, SWING_LIFT);
        let (s1, p1) = swing(a, b, -V3::X, 1.0, SWING_LIFT);
        assert!((s0 - rolled_ankle(a, -V3::X, -TOE_OFF)).length() < 1e-5);
        assert!((s1 - rolled_ankle(b, -V3::X, HEEL_STRIKE)).length() < 1e-5);
        assert_eq!((p0, p1), (-TOE_OFF, HEEL_STRIKE));
        let (mid, _) = swing(a, b, -V3::X, 0.5, SWING_LIFT);
        assert!(mid.y > 0.1, "foot clears the floor");
    }
}
