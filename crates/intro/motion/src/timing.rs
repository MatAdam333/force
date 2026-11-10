//! Every timing constant of the animation, in seconds from launch. Retime shots here only: the
//! timeline, camera path and pose tracks read these and contain no numbers of their own.
//! Keep each group in increasing order; `tests::timings_are_ordered` checks it.

/// Frames per second of the fixed timestep.
pub const FPS: f32 = 60.0;
/// Total length; the last frame is fully faded into the app's screen.
pub const TOTAL: f32 = 8.76667;

// ---- Shot 1: establishing (side view, wide) -------------------------------------------------
/// Picture fades up from black over this time.
pub const FADE_IN_END: f32 = 0.8;
/// Letterbox bars slide in from top and bottom.
pub const LETTERBOX_IN: (f32, f32) = (0.0, 0.9);

// ---- Shot 2: the walk-in -------------------------------------------------------------------
/// He walks in from just off-screen right to his stop mark in front of the chair (about 2 m).
pub const WALK: (f32, f32) = (0.35, 2.3);
/// Fraction of the walk spent at full speed; the rest decelerates smoothly to a stop.
pub const WALK_CRUISE_FRACTION: f32 = 0.6;
/// Slow camera drift toward the chair while he walks.
pub const DRIFT: (f32, f32) = (0.35, 1.9);

// ---- Shot 3: orbit side -> front ----------------------------------------------------------
/// Camera orbits ~90 degrees around the chair while dollying in; it arrives at the front view as
/// he begins to lower himself, so the shot never stands still between the turn and the sit.
pub const ORBIT: (f32, f32) = (1.9, 3.64);
/// He turns his back to the chair (ends facing the camera).
pub const TURN: (f32, f32) = (2.3, 3.2);

// ---- Shot 4: the sit-down, front view ------------------------------------------------------
// Each beat's pose is reached at the end of its window. One smooth curve runs through all of
// them, so the body slows into each beat but never stops dead between beats.
/// a. Reaches back and plants both hands on the armrests; starts as the turn's body motion ends
/// (`gait::TURN_POSE_END`), while his feet finish the turn.
pub const SIT_REACH: (f32, f32) = (3.01667, 3.63667);
/// b. Leans forward, bends the knees and lowers himself, weight through the arms.
pub const SIT_LOWER: (f32, f32) = (3.63667, 4.31667);
/// ... and the last part of the descent onto the cushion.
pub const SIT_LAND: (f32, f32) = (4.31667, 4.76667);
/// Cushion compression on contact (starts as he lands, springs a little, stays compressed).
pub const CUSHION: (f32, f32) = (4.46667, 4.91667);
/// c. Sits up and looks down at his lap: the laptop comes onto it and he opens it (the hands
/// are animated in blender/scene.py).
pub const SIT_SETTLE: (f32, f32) = (4.76667, 5.51667);
/// d. Leans back against the lounge chair's backrest, still looking at the laptop.
pub const RECLINE: (f32, f32) = (5.71667, 6.71667);
/// Getting comfortable: one slow weight shift from hip to hip, once he is back.
pub const COMFORT: (f32, f32) = (6.61667, 7.81667);

// ---- Shot 5: hold and hand-off -------------------------------------------------------------
/// The final hold (the camera goes behind him to the laptop screen: blender/scene.py).
pub const HOLD: (f32, f32) = (6.71667, 8.76667);
/// Handheld drift fades in over this time at the start of the hold.
pub const HANDHELD_RAMP: f32 = 0.6;
/// Fade into the app's main screen.
pub const FADE_OUT: (f32, f32) = (8.06667, 8.76667);

pub fn total_frames() -> u64 {
    (TOTAL * FPS).round() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timings_are_ordered() {
        let windows = [
            LETTERBOX_IN,
            WALK,
            DRIFT,
            ORBIT,
            TURN,
            SIT_REACH,
            SIT_LOWER,
            SIT_LAND,
            CUSHION,
            SIT_SETTLE,
            RECLINE,
            COMFORT,
            HOLD,
            FADE_OUT,
        ];
        for (a, b) in windows {
            assert!(a < b && a >= 0.0 && b <= TOTAL, "bad window {a}..{b}");
        }
        // the sit-down beats follow each other
        // the sit-down starts as the turn's body motion ends; its feet and facing finish after
        let reach_from = TURN.0 + crate::gait::TURN_POSE_END * (TURN.1 - TURN.0);
        assert!((SIT_REACH.0 - reach_from).abs() < 0.011 && SIT_REACH.0 < TURN.1);
        assert!(SIT_REACH.1 <= SIT_LOWER.0 && SIT_LOWER.1 <= SIT_LAND.0);
        assert!(SIT_LAND.1 <= SIT_SETTLE.0 && SIT_SETTLE.1 <= RECLINE.0);
        assert!(RECLINE.1 <= HOLD.1 && COMFORT.1 <= HOLD.1);
        assert!(WALK.0 < TURN.0 && WALK.1 <= TURN.0);
        assert!((0.0..1.0).contains(&WALK_CRUISE_FRACTION));
        assert_eq!(total_frames(), 526);
    }
}
