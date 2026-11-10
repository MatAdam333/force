//! The man: proportions, the [`Pose`] type (keyframed joint rotations), forward kinematics, and a
//! two-bone IK pass that keeps hands and feet on their contact points while a pose is pinned.
//!
//! Body frame: forward = +X, up = +Y, his right = +Z (rotated by the body yaw). Index 0 of the
//! per-side arrays is his left, 1 his right.

use super::math::{lerp, rad, v3, M3, V3};

// ---- Proportions (metres) ---------------------------------------------------------------------
pub const HIP_HEIGHT: f32 = 0.98; // pelvis centre above the floor when standing straight
pub const HIP_DROP: f32 = 0.05; // hip joints below the pelvis centre
pub const HIP_W: f32 = 0.10; // hip joints from the midline
pub const LOWER_SPINE: f32 = 0.28; // pelvis centre -> mid back
pub const UPPER_SPINE: f32 = 0.22; // mid back -> neck base
pub const NECK: f32 = 0.09;
pub const HEAD_R: f32 = 0.105;
pub const SHOULDER_W: f32 = 0.2; // shoulder joints from the midline
pub const SHOULDER_DOWN: f32 = 0.035; // shoulder joints below the neck base
pub const UPPER_ARM: f32 = 0.30;
pub const FORE_ARM: f32 = 0.31; // elbow -> centre of the hand
pub const THIGH: f32 = 0.44;
pub const SHIN: f32 = 0.42;
pub const TOE: f32 = 0.17; // ankle -> toe (along the foot)
pub const HEEL: f32 = 0.05; // ankle -> heel
pub const FOOT_DOWN: f32 = 0.03; // foot centreline below the ankle

/// Number of animated values in a [`Pose`].
pub const POSE_CHANNELS: usize = 25;

/// One arm: angles in degrees, relative to the chest.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Arm {
    /// Shoulder swing: + forward, - backward (0 = hanging).
    pub swing: f32,
    /// Shoulder abduction: + away from the body.
    pub out: f32,
    /// Elbow bend: + bends the forearm forward/up.
    pub elbow: f32,
}

/// One leg: angles in degrees, relative to upright (not to the leaning trunk).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Leg {
    /// Hip swing: + thigh forward, - backward.
    pub swing: f32,
    /// Hip abduction: + away from the midline.
    pub out: f32,
    /// Knee bend: + folds the shin backward.
    pub knee: f32,
    /// Ankle: + toes up, - toes down.
    pub ankle: f32,
}

/// A keyframe of the whole body. See `poses.rs` for the named poses and how to tweak them.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pose {
    /// Pelvis centre relative to the point between his feet: [forward, up, right] in metres.
    pub pelvis: [f32; 3],
    /// Trunk lean at the hips, degrees: + forward, - reclined.
    pub lean: f32,
    /// Sideways tilt of the trunk, degrees: + toward his right.
    pub tilt: f32,
    /// Extra bend of the upper back, degrees: + hunched forward.
    pub spine: f32,
    /// Head pitch, degrees: + chin down.
    pub head: f32,
    /// Shoulders lowered (metres): the exhale drop.
    pub shoulder_drop: f32,
    pub l_arm: Arm,
    pub r_arm: Arm,
    pub l_leg: Leg,
    pub r_leg: Leg,
    /// 0..1: how strongly the hands are held on their contact points by IK (0 = pure joint angles).
    pub hand_pin: f32,
    /// 0..1: contact point moves from the armrest grip (0) to forearms resting along the armrests (1).
    pub hand_rest: f32,
    /// 0..1: how strongly the feet are held on the floor where he stands.
    pub foot_pin: f32,
}

fn lerp_arm(a: &Arm, b: &Arm, t: f32) -> Arm {
    Arm {
        swing: lerp(a.swing, b.swing, t),
        out: lerp(a.out, b.out, t),
        elbow: lerp(a.elbow, b.elbow, t),
    }
}

fn lerp_leg(a: &Leg, b: &Leg, t: f32) -> Leg {
    Leg {
        swing: lerp(a.swing, b.swing, t),
        out: lerp(a.out, b.out, t),
        knee: lerp(a.knee, b.knee, t),
        ankle: lerp(a.ankle, b.ankle, t),
    }
}

impl Pose {
    /// Component-wise interpolation; the caller applies easing to `t`.
    pub fn lerp(&self, o: &Pose, t: f32) -> Pose {
        Pose {
            pelvis: [
                lerp(self.pelvis[0], o.pelvis[0], t),
                lerp(self.pelvis[1], o.pelvis[1], t),
                lerp(self.pelvis[2], o.pelvis[2], t),
            ],
            lean: lerp(self.lean, o.lean, t),
            tilt: lerp(self.tilt, o.tilt, t),
            spine: lerp(self.spine, o.spine, t),
            head: lerp(self.head, o.head, t),
            shoulder_drop: lerp(self.shoulder_drop, o.shoulder_drop, t),
            l_arm: lerp_arm(&self.l_arm, &o.l_arm, t),
            r_arm: lerp_arm(&self.r_arm, &o.r_arm, t),
            l_leg: lerp_leg(&self.l_leg, &o.l_leg, t),
            r_leg: lerp_leg(&self.r_leg, &o.r_leg, t),
            hand_pin: lerp(self.hand_pin, o.hand_pin, t),
            hand_rest: lerp(self.hand_rest, o.hand_rest, t),
            foot_pin: lerp(self.foot_pin, o.foot_pin, t),
        }
    }

    /// Every field as one flat list (for per-channel curves), in a fixed order.
    pub fn channels(&self) -> [f32; POSE_CHANNELS] {
        let a = |a: &Arm| [a.swing, a.out, a.elbow];
        let l = |l: &Leg| [l.swing, l.out, l.knee, l.ankle];
        let (la, ra, ll, rl) = (
            a(&self.l_arm),
            a(&self.r_arm),
            l(&self.l_leg),
            l(&self.r_leg),
        );
        [
            self.pelvis[0],
            self.pelvis[1],
            self.pelvis[2],
            self.lean,
            self.tilt,
            self.spine,
            self.head,
            self.shoulder_drop,
            la[0],
            la[1],
            la[2],
            ra[0],
            ra[1],
            ra[2],
            ll[0],
            ll[1],
            ll[2],
            ll[3],
            rl[0],
            rl[1],
            rl[2],
            rl[3],
            self.hand_pin,
            self.hand_rest,
            self.foot_pin,
        ]
    }

    /// Inverse of [`Pose::channels`].
    pub fn from_channels(c: &[f32; POSE_CHANNELS]) -> Pose {
        let arm = |i: usize| Arm {
            swing: c[i],
            out: c[i + 1],
            elbow: c[i + 2],
        };
        let leg = |i: usize| Leg {
            swing: c[i],
            out: c[i + 1],
            knee: c[i + 2],
            ankle: c[i + 3],
        };
        Pose {
            pelvis: [c[0], c[1], c[2]],
            lean: c[3],
            tilt: c[4],
            spine: c[5],
            head: c[6],
            shoulder_drop: c[7],
            l_arm: arm(8),
            r_arm: arm(11),
            l_leg: leg(14),
            r_leg: leg(18),
            hand_pin: c[22],
            hand_rest: c[23],
            foot_pin: c[24],
        }
    }

    /// Left/right mirror image (used by tests to check the walk cycle is symmetric).
    pub fn mirrored(&self) -> Pose {
        Pose {
            pelvis: [self.pelvis[0], self.pelvis[1], -self.pelvis[2]],
            tilt: -self.tilt,
            l_arm: self.r_arm,
            r_arm: self.l_arm,
            l_leg: self.r_leg,
            r_leg: self.l_leg,
            ..*self
        }
    }
}

/// World-space contact points the IK pins hands and feet to. Index 0 = his left.
#[derive(Clone, Copy, Debug)]
pub struct Pins {
    /// Hand centres when gripping the armrests.
    pub grip: [V3; 2],
    /// Front end of each armrest's resting line (forearm centre height), and the unit direction
    /// pointing back along the armrest.
    pub rest: [(V3, V3); 2],
    /// Ankle positions on the floor.
    pub feet: [V3; 2],
    /// Orientation of each pinned foot: column 0 = toward the toes, column 1 = up from the sole.
    pub foot_frame: [M3; 2],
}

impl Pins {
    pub fn none() -> Pins {
        Pins {
            grip: [V3::ZERO; 2],
            rest: [(V3::ZERO, -V3::X); 2],
            feet: [V3::ZERO; 2],
            foot_frame: [M3::I; 2],
        }
    }
}

/// Solved skeleton in world space: what the renderer turns into capsules and boxes.
#[derive(Clone, Copy, Debug)]
pub struct Joints {
    pub body: M3,
    pub trunk: M3,
    pub chest: M3,
    pub head_frame: M3,
    pub pelvis: V3,
    pub spine_mid: V3,
    pub neck: V3,
    pub neck_top: V3,
    pub head: V3,
    pub shoulder: [V3; 2],
    pub elbow: [V3; 2],
    pub hand: [V3; 2],
    pub hip: [V3; 2],
    pub knee: [V3; 2],
    pub ankle: [V3; 2],
    pub heel: [V3; 2],
    pub toe: [V3; 2],
}

impl Joints {
    pub fn all_points(&self) -> Vec<V3> {
        let mut v = vec![
            self.pelvis,
            self.spine_mid,
            self.neck,
            self.neck_top,
            self.head,
        ];
        for s in 0..2 {
            v.extend([
                self.shoulder[s],
                self.elbow[s],
                self.hand[s],
                self.hip[s],
                self.knee[s],
                self.ankle[s],
                self.heel[s],
                self.toe[s],
            ]);
        }
        v
    }
}

/// Fraction of full limb length where two-bone IK starts easing off (soft IK).
const SOFT_IK: f32 = 0.95;
/// Legs use only part of that zone: a standing leg is nearly straight and must still reach the
/// floor.
const LEG_SOFT: f32 = 0.35;

/// Two-bone IK: from `root`, bones `l1` then `l2`, reach `target`, bending toward `pole`.
/// `soft` (0..1) is how much of the soft extension limit applies (0 = hard clamp: a target the
/// limb can reach is met exactly, which keeps a lightly weighted pin identical to the FK pose).
/// Returns (middle joint, end). Unreachable targets are approached along the root->target line;
/// a pole parallel to that line falls back to `fallback`.
pub fn two_bone(
    root: V3,
    target: V3,
    l1: f32,
    l2: f32,
    pole: V3,
    fallback: V3,
    soft: f32,
) -> (V3, V3) {
    let d = target - root;
    let len = d.length();
    let dir = if len > 1e-6 { d / len } else { -V3::Y };
    // soft limit near full extension: approaching a straight limb, the reach eases off instead
    // of locking, so the middle joint never snaps from straight to bent
    let full = l1 + l2 - 1e-4;
    let knee = full * (1.0 - (1.0 - SOFT_IK) * soft.clamp(0.0, 1.0));
    let reach = if len > knee && full - knee > 1e-6 {
        knee + (full - knee) * (1.0 - (-(len - knee) / (full - knee)).exp())
    } else {
        len
    };
    let dist = reach.clamp((l1 - l2).abs() + 1e-4, full);
    let a = (l1 * l1 + dist * dist - l2 * l2) / (2.0 * dist);
    let h = (l1 * l1 - a * a).max(0.0).sqrt();
    let perp = |p: V3| p - dir * p.dot(dir);
    let mut bend = perp(pole);
    if bend.length() < 1e-4 {
        bend = perp(fallback);
    }
    let bend = bend.normalize();
    let mid = root + dir * a + bend * h;
    (mid, root + dir * dist)
}

/// Hand target that lays the forearm along the armrest line: the elbow sits on the line at
/// upper-arm length from the shoulder, the hand one forearm further toward the front end.
/// Returns (hand target, elbow position used as the IK pole).
pub fn rest_on_line(shoulder: V3, front: V3, back_dir: V3) -> (V3, V3) {
    // elbow E(s) = front + back_dir * s with |E - shoulder| = UPPER_ARM
    let w = front - shoulder;
    let b = back_dir.dot(w);
    let c = w.dot(w) - UPPER_ARM * UPPER_ARM;
    let disc = b * b - c;
    let s = if disc >= 0.0 {
        let r = disc.sqrt();
        let (s1, s2) = (-b - r, -b + r);
        // prefer the elbow that leaves the hand closest to the armrest end
        if (s1 - FORE_ARM).abs() <= (s2 - FORE_ARM).abs() {
            s1
        } else {
            s2
        }
    } else {
        -b // unreachable: closest point on the line; IK will stretch toward it
    };
    // hands may hang a little past the end of the armrest, not more
    let s = s.max(FORE_ARM - 0.06);
    let elbow = front + back_dir * s;
    (elbow - back_dir * FORE_ARM, elbow)
}

/// Forward kinematics of `pose` standing at `base` (floor point between the feet) facing `yaw_deg`
/// (0 = +X), followed by the IK pins the pose asks for.
pub fn solve(pose: &Pose, base: V3, yaw_deg: f32, pins: &Pins) -> Joints {
    let body = M3::rot_y(rad(yaw_deg));
    let [pf, pu, ps] = pose.pelvis;
    let pelvis = base + body * v3(pf, pu, ps);
    let trunk = body * M3::rot_z(rad(-pose.lean)) * M3::rot_x(rad(pose.tilt));
    let chest = trunk * M3::rot_z(rad(-pose.spine));
    let head_frame = chest * M3::rot_z(rad(-pose.head));
    let spine_mid = pelvis + trunk * v3(0.0, LOWER_SPINE, 0.0);
    let neck = spine_mid + chest * v3(0.0, UPPER_SPINE, 0.0);
    let neck_top = neck + head_frame * v3(0.0, NECK, 0.0);
    let head = neck_top + head_frame * v3(0.015, HEAD_R, 0.0);

    let fwd = body.col(0);
    let right = body.col(2);
    let mut j = Joints {
        body,
        trunk,
        chest,
        head_frame,
        pelvis,
        spine_mid,
        neck,
        neck_top,
        head,
        shoulder: [V3::ZERO; 2],
        elbow: [V3::ZERO; 2],
        hand: [V3::ZERO; 2],
        hip: [V3::ZERO; 2],
        knee: [V3::ZERO; 2],
        ankle: [V3::ZERO; 2],
        heel: [V3::ZERO; 2],
        toe: [V3::ZERO; 2],
    };

    for side in 0..2 {
        let s = if side == 0 { -1.0 } else { 1.0 };
        let (arm, leg) = if side == 0 {
            (&pose.l_arm, &pose.l_leg)
        } else {
            (&pose.r_arm, &pose.r_leg)
        };
        let out_side = right * s;

        // ---- arm
        let shoulder = neck + chest * v3(0.0, -SHOULDER_DOWN - pose.shoulder_drop, s * SHOULDER_W);
        let upper = chest * M3::rot_z(rad(arm.swing)) * M3::rot_x(rad(-s * arm.out));
        let fore = upper * M3::rot_z(rad(arm.elbow));
        let elbow_fk = shoulder + upper * v3(0.0, -UPPER_ARM, 0.0);
        let hand_fk = elbow_fk + fore * v3(0.0, -FORE_ARM, 0.0);
        let (elbow, hand) = if pose.hand_pin > 0.0 {
            let (rest_hand, rest_elbow) =
                rest_on_line(shoulder, pins.rest[side].0, pins.rest[side].1);
            let pin = pins.grip[side].lerp(rest_hand, pose.hand_rest);
            let target = hand_fk.lerp(pin, pose.hand_pin);
            // gripping behind him: elbows point back and out; resting: toward the elbow on the line
            let grip_pole = -fwd + out_side * 0.8 + V3::Y * 0.2;
            let pin_pole = grip_pole
                .normalize()
                .lerp((rest_elbow - shoulder).normalize(), pose.hand_rest);
            let fk_pole = (elbow_fk - shoulder).normalize();
            let pole = fk_pole.lerp(pin_pole, pose.hand_pin);
            two_bone(
                shoulder,
                target,
                UPPER_ARM,
                FORE_ARM,
                pole,
                grip_pole,
                pose.hand_pin,
            )
        } else {
            (elbow_fk, hand_fk)
        };

        // ---- leg
        let hip = pelvis + trunk * v3(0.0, -HIP_DROP, s * HIP_W);
        let thigh = body * M3::rot_z(rad(leg.swing)) * M3::rot_x(rad(-s * leg.out));
        let shin = thigh * M3::rot_z(rad(-leg.knee));
        let foot = shin * M3::rot_z(rad(leg.ankle));
        let knee_fk = hip + thigh * v3(0.0, -THIGH, 0.0);
        let ankle_fk = knee_fk + shin * v3(0.0, -SHIN, 0.0);
        let (knee, ankle) = if pose.foot_pin > 0.0 {
            let target = ankle_fk.lerp(pins.feet[side], pose.foot_pin);
            let knee_pole = fwd + V3::Y * 0.2 + out_side * 0.25;
            let fk_pole = (knee_fk - hip).normalize();
            let pole = fk_pole.lerp(knee_pole.normalize(), pose.foot_pin);
            two_bone(
                hip,
                target,
                THIGH,
                SHIN,
                pole,
                knee_pole,
                pose.foot_pin * LEG_SOFT,
            )
        } else {
            (knee_fk, ankle_fk)
        };
        // pinned feet take the orientation of their contact (flat, or rolling heel to toe)
        let pf = pins.foot_frame[side];
        let foot_fwd = foot.col(0).lerp(pf.col(0), pose.foot_pin).normalize();
        let foot_down = (-foot.col(1)).lerp(-pf.col(1), pose.foot_pin).normalize();

        j.shoulder[side] = shoulder;
        j.elbow[side] = elbow;
        j.hand[side] = hand;
        j.hip[side] = hip;
        j.knee[side] = knee;
        j.ankle[side] = ankle;
        j.heel[side] = ankle - foot_fwd * HEEL + foot_down * FOOT_DOWN;
        j.toe[side] = ankle + foot_fwd * TOE + foot_down * FOOT_DOWN;
    }
    j
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::poses;

    #[test]
    fn standing_feet_touch_the_floor() {
        let mut straight = poses::STAND;
        straight.pelvis[1] = HIP_HEIGHT;
        straight.foot_pin = 0.0;
        let j = solve(&straight, V3::ZERO, 0.0, &Pins::none());
        for s in 0..2 {
            assert!((j.ankle[s].y - 0.07).abs() < 0.02, "ankle {:?}", j.ankle[s]);
            assert!(j.toe[s].x > j.heel[s].x, "toes point forward");
        }
        assert!(j.head.y > 1.6 && j.head.y < 1.8, "head height {}", j.head.y);
        assert!(
            j.shoulder[1].z > 0.0 && j.shoulder[0].z < 0.0,
            "right is +Z at yaw 0"
        );
    }

    #[test]
    fn yaw_turns_the_body() {
        let mut straight = poses::STAND;
        straight.foot_pin = 0.0;
        let j = solve(&straight, V3::ZERO, 180.0, &Pins::none());
        assert!(j.toe[0].x < j.heel[0].x, "facing -X at yaw 180");
    }

    #[test]
    fn two_bone_keeps_lengths_and_reaches() {
        let root = v3(0.0, 1.0, 0.0);
        let target = v3(0.3, 0.6, 0.1);
        let (mid, end) = two_bone(root, target, 0.44, 0.42, V3::X, V3::X, 1.0);
        assert!(((mid - root).length() - 0.44).abs() < 1e-4);
        assert!(((end - mid).length() - 0.42).abs() < 1e-4);
        assert!((end - target).length() < 1e-4);
        assert!(mid.x > 0.15, "bends toward the pole");
        // unreachable target: stays on the line, lengths kept
        let (mid, end) = two_bone(root, v3(0.0, -2.0, 0.0), 0.44, 0.42, V3::X, V3::X, 1.0);
        assert!(((mid - root).length() - 0.44).abs() < 1e-3);
        assert!(end.x.abs() < 1e-3);
        // degenerate pole falls back, no NaN
        let (mid, _) = two_bone(root, v3(0.0, 0.4, 0.0), 0.44, 0.42, -V3::Y, V3::Z, 1.0);
        assert!(mid.is_finite() && mid.z > 0.0);
    }

    #[test]
    fn pins_hold_hands_and_feet() {
        let pins = Pins {
            grip: [v3(-0.3, 0.7, -0.35), v3(-0.3, 0.7, 0.35)],
            rest: [
                (v3(-0.1, 0.7, -0.33), -V3::X),
                (v3(-0.1, 0.7, 0.33), -V3::X),
            ],
            feet: [v3(0.0, 0.07, -0.12), v3(0.0, 0.07, 0.12)],
            foot_frame: [M3::I; 2],
        };
        let j = solve(&poses::LOWERING, V3::ZERO, 0.0, &pins);
        for s in 0..2 {
            assert!(
                (j.hand[s] - pins.grip[s]).length() < 0.01,
                "hand {s} {:?}",
                j.hand[s]
            );
            assert!((j.ankle[s] - pins.feet[s]).length() < 0.01);
            assert!(((j.elbow[s] - j.shoulder[s]).length() - UPPER_ARM).abs() < 1e-3);
            assert!(j.knee[s].x > j.hip[s].x, "knees point forward");
        }
    }

    #[test]
    fn rest_lays_forearm_along_the_line() {
        let shoulder = v3(0.0, 0.95, 0.19);
        let front = v3(0.45, 0.70, 0.33);
        let (hand, elbow) = rest_on_line(shoulder, front, -V3::X);
        assert!(((elbow - shoulder).length() - UPPER_ARM).abs() < 1e-3);
        assert!((elbow.y - 0.70).abs() < 1e-4 && (hand.y - 0.70).abs() < 1e-4);
        assert!(((hand - elbow).length() - FORE_ARM).abs() < 1e-4);
        assert!(hand.x > elbow.x);
    }

    #[test]
    fn mirror_twice_is_identity() {
        assert_eq!(poses::WALK_A.mirrored().mirrored(), poses::WALK_A);
    }
}
