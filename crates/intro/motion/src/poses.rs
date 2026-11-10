//! Named keyframe poses. Edit the numbers here to change how he moves; the timeline decides
//! when each pose is reached (`timing.rs`) and fits smooth curves through them.
//!
//! Conventions (see `skeleton.rs`): angles in degrees, lengths in metres.
//! - `pelvis`: [forward, up, right] of the pelvis centre from the point between his feet.
//!   Standing straight is [0, 0.98, 0]; sitting moves it back (negative forward) and down.
//! - `lean` + forward / - reclined; `tilt` + toward his right; `spine` + hunch; `head` + chin down.
//! - Arms: `swing` + forward, `out` away from the body, `elbow` bend (relative to the chest).
//! - Legs: `swing` + forward, `out` away from the midline, `knee` bend, `ankle` + toes up
//!   (relative to upright).
//! - `hand_pin` / `foot_pin` 0..1: IK holds hands on the armrests / feet on the floor. When a pin
//!   is 1 the joint angles of that limb only shape the transition into the pose.
//! - `hand_rest` 0..1: the hand contact moves from gripping the armrest fronts (0) to the
//!   forearms lying along the armrests (1).

use super::skeleton::{Arm, Leg, Pose};

const ARM_HANG: Arm = Arm {
    swing: 3.0,
    out: 5.0,
    elbow: 10.0,
};
const LEG_STRAIGHT: Leg = Leg {
    swing: 0.0,
    out: 2.0,
    knee: 0.0,
    ankle: 0.0,
};

/// Standing still, relaxed: feet planted, knees soft (not locked straight).
pub const STAND: Pose = Pose {
    pelvis: [0.0, 0.977, 0.0],
    lean: 2.0,
    tilt: 0.0,
    spine: 2.0,
    head: 0.0,
    shoulder_drop: 0.0,
    l_arm: ARM_HANG,
    r_arm: ARM_HANG,
    l_leg: LEG_STRAIGHT,
    r_leg: LEG_STRAIGHT,
    hand_pin: 0.0,
    hand_rest: 0.0,
    foot_pin: 1.0,
};

// ---- Walk cycle: A -> PASS_A -> B -> PASS_B, one full cycle per stride (gait::STRIDE). The legs
// ---- of these poses only shape the IK; the feet themselves come from the footstep planner. ----

/// Contact: left heel strikes in front, right leg pushes off behind; right arm forward.
/// The pelvis is lowest here (vertical bob).
pub const WALK_A: Pose = Pose {
    pelvis: [0.0, 0.952, -0.006],
    lean: 5.0,
    tilt: 0.0,
    spine: 2.0,
    head: -2.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: -20.0,
        out: 6.0,
        elbow: 8.0,
    },
    r_arm: Arm {
        swing: 18.0,
        out: 6.0,
        elbow: 24.0,
    },
    l_leg: Leg {
        swing: 24.0,
        out: 1.5,
        knee: 4.0,
        ankle: 10.0,
    },
    r_leg: Leg {
        swing: -21.0,
        out: 1.5,
        knee: 14.0,
        ankle: -14.0,
    },
    hand_pin: 0.0,
    hand_rest: 0.0,
    foot_pin: 0.0,
};

/// Passing: weight over the left leg, right leg swings through with a bent knee.
/// The pelvis is highest here and shifted over the standing leg (weight shift).
pub const WALK_PASS_A: Pose = Pose {
    pelvis: [0.0, 0.982, -0.022],
    lean: 4.0,
    tilt: -2.5,
    spine: 2.0,
    head: -1.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: -2.0,
        out: 5.0,
        elbow: 12.0,
    },
    r_arm: Arm {
        swing: 2.0,
        out: 5.0,
        elbow: 16.0,
    },
    l_leg: Leg {
        swing: -3.0,
        out: 1.5,
        knee: 6.0,
        ankle: 0.0,
    },
    r_leg: Leg {
        swing: 14.0,
        out: 1.5,
        knee: 44.0,
        ankle: 4.0,
    },
    hand_pin: 0.0,
    hand_rest: 0.0,
    foot_pin: 0.0,
};

/// Contact on the other foot (mirror of WALK_A).
pub const WALK_B: Pose = Pose {
    pelvis: [0.0, 0.952, 0.006],
    lean: 5.0,
    tilt: 0.0,
    spine: 2.0,
    head: -2.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: 18.0,
        out: 6.0,
        elbow: 24.0,
    },
    r_arm: Arm {
        swing: -20.0,
        out: 6.0,
        elbow: 8.0,
    },
    l_leg: Leg {
        swing: -21.0,
        out: 1.5,
        knee: 14.0,
        ankle: -14.0,
    },
    r_leg: Leg {
        swing: 24.0,
        out: 1.5,
        knee: 4.0,
        ankle: 10.0,
    },
    hand_pin: 0.0,
    hand_rest: 0.0,
    foot_pin: 0.0,
};

/// Passing on the other side (mirror of WALK_PASS_A).
pub const WALK_PASS_B: Pose = Pose {
    pelvis: [0.0, 0.982, 0.022],
    lean: 4.0,
    tilt: 2.5,
    spine: 2.0,
    head: -1.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: 2.0,
        out: 5.0,
        elbow: 16.0,
    },
    r_arm: Arm {
        swing: -2.0,
        out: 5.0,
        elbow: 12.0,
    },
    l_leg: Leg {
        swing: 14.0,
        out: 1.5,
        knee: 44.0,
        ankle: 4.0,
    },
    r_leg: Leg {
        swing: -3.0,
        out: 1.5,
        knee: 6.0,
        ankle: 0.0,
    },
    hand_pin: 0.0,
    hand_rest: 0.0,
    foot_pin: 0.0,
};

/// Middle of the turn: a small step with the left foot, arms slightly out for balance.
/// The timeline blends STAND -> TURN -> STAND while the body yaw swings 180 degrees.
pub const TURN: Pose = Pose {
    pelvis: [0.0, 0.962, -0.015],
    lean: 3.0,
    tilt: -2.0,
    spine: 3.0,
    head: 4.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: 8.0,
        out: 12.0,
        elbow: 22.0,
    },
    r_arm: Arm {
        swing: -6.0,
        out: 12.0,
        elbow: 18.0,
    },
    l_leg: Leg {
        swing: 10.0,
        out: 6.0,
        knee: 26.0,
        ankle: 2.0,
    },
    r_leg: Leg {
        swing: -4.0,
        out: 4.0,
        knee: 5.0,
        ankle: 0.0,
    },
    hand_pin: 0.0,
    hand_rest: 0.0,
    foot_pin: 0.0,
};

/// Beat a: knees bent, leaning forward, both hands reached back onto the armrest fronts.
pub const HANDS_ON_ARMS: Pose = Pose {
    pelvis: [-0.38, 0.66, 0.0],
    lean: 30.0,
    tilt: 0.0,
    spine: 6.0,
    head: -20.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: -45.0,
        out: 20.0,
        elbow: 20.0,
    },
    r_arm: Arm {
        swing: -45.0,
        out: 20.0,
        elbow: 20.0,
    },
    l_leg: Leg {
        swing: 30.0,
        out: 6.0,
        knee: 55.0,
        ankle: 0.0,
    },
    r_leg: Leg {
        swing: 30.0,
        out: 6.0,
        knee: 55.0,
        ankle: 0.0,
    },
    hand_pin: 1.0,
    hand_rest: 0.0,
    foot_pin: 1.0,
};

/// Beat b: deep crouch over the seat, bottom pushed back, weight through the arms (elbows bend).
pub const LOWERING: Pose = Pose {
    pelvis: [-0.56, 0.60, 0.0],
    lean: 32.0,
    tilt: 0.0,
    spine: 10.0,
    head: -26.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: -25.0,
        out: 22.0,
        elbow: 70.0,
    },
    r_arm: Arm {
        swing: -25.0,
        out: 22.0,
        elbow: 70.0,
    },
    l_leg: Leg {
        swing: 60.0,
        out: 7.0,
        knee: 100.0,
        ankle: 0.0,
    },
    r_leg: Leg {
        swing: 60.0,
        out: 7.0,
        knee: 100.0,
        ankle: 0.0,
    },
    hand_pin: 1.0,
    hand_rest: 0.0,
    foot_pin: 1.0,
};

/// Contact: on the cushion, sitting upright, hands still on the armrests.
pub const SEATED_UPRIGHT: Pose = Pose {
    pelvis: [-0.63, 0.535, 0.0],
    lean: 9.0,
    tilt: 0.0,
    spine: 6.0,
    head: -8.0,
    shoulder_drop: 0.0,
    l_arm: Arm {
        swing: -10.0,
        out: 22.0,
        elbow: 75.0,
    },
    r_arm: Arm {
        swing: -10.0,
        out: 22.0,
        elbow: 75.0,
    },
    l_leg: Leg {
        swing: 85.0,
        out: 7.0,
        knee: 85.0,
        ankle: 0.0,
    },
    r_leg: Leg {
        swing: 85.0,
        out: 7.0,
        knee: 85.0,
        ankle: 0.0,
    },
    hand_pin: 1.0,
    hand_rest: 0.0,
    foot_pin: 1.0,
};

/// Beat c: settled back into the backrest, shoulders dropped (the exhale). Hands still on the
/// armrest fronts; the wiggle is added by the timeline on the way here.
pub const SEATED_SETTLED: Pose = Pose {
    pelvis: [-0.64, 0.525, 0.0],
    lean: 6.0,
    tilt: 0.0,
    spine: 6.0,
    head: 24.0,
    shoulder_drop: 0.02,
    l_arm: Arm {
        swing: 5.0,
        out: 20.0,
        elbow: 70.0,
    },
    r_arm: Arm {
        swing: 5.0,
        out: 20.0,
        elbow: 70.0,
    },
    l_leg: Leg {
        swing: 85.0,
        out: 8.0,
        knee: 85.0,
        ankle: 0.0,
    },
    r_leg: Leg {
        swing: 85.0,
        out: 8.0,
        knee: 85.0,
        ankle: 0.0,
    },
    hand_pin: 1.0,
    hand_rest: 0.0,
    foot_pin: 1.0,
};

/// Beat d: back against the reclined backrest, upper back and head a little forward, looking at
/// the laptop on his lap (the hands are on the laptop: blender/scene.py).
pub const SEATED_RELAXED: Pose = Pose {
    pelvis: [-0.67, 0.515, 0.0],
    lean: -28.0,
    tilt: 0.0,
    spine: 8.0,
    head: 30.0,
    shoulder_drop: 0.045,
    l_arm: Arm {
        swing: 20.0,
        out: 18.0,
        elbow: 70.0,
    },
    r_arm: Arm {
        swing: 20.0,
        out: 18.0,
        elbow: 70.0,
    },
    l_leg: Leg {
        swing: 85.0,
        out: 8.0,
        knee: 85.0,
        ankle: 0.0,
    },
    r_leg: Leg {
        swing: 85.0,
        out: 8.0,
        knee: 85.0,
        ankle: 0.0,
    },
    hand_pin: 1.0,
    hand_rest: 1.0,
    foot_pin: 1.0,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn walk_cycle_is_symmetric() {
        assert_eq!(WALK_A.mirrored(), WALK_B);
        assert_eq!(WALK_PASS_A.mirrored(), WALK_PASS_B);
    }

    #[test]
    fn pins_are_in_range() {
        for p in [
            STAND,
            WALK_A,
            WALK_PASS_A,
            WALK_B,
            WALK_PASS_B,
            TURN,
            HANDS_ON_ARMS,
            LOWERING,
            SEATED_UPRIGHT,
            SEATED_SETTLED,
            SEATED_RELAXED,
        ] {
            for w in [p.hand_pin, p.hand_rest, p.foot_pin] {
                assert!((0.0..=1.0).contains(&w));
            }
        }
    }
}
