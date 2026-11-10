//! Playback timing. The shots themselves (walk, turn, sit, camera moves) are baked into the film:
//! retime them in `motion/src/timing.rs` and the laptop/camera beats in `blender/scene.py`, and
//! re-render. Here: the frame rates, the length, the splash's beats and the fades the player adds
//! on top.

/// Frames per second of the embedded film, and the most the player shows: a terminal that keeps up
/// gets every frame. One that doesn't drops frames on a steady lower rate (see `term.rs`); the
/// timeline itself always runs in real time.
pub const FPS: f32 = 60.0;
/// The steady rate for a terminal that can't take [`FPS`].
pub const SLOW_FPS: f32 = 30.0;
/// Total length; the last frame is fully faded into the app's screen.
pub const TOTAL: f32 = 11.71667;
/// Frames in the embedded film (it ends with the laptop's screen filling the picture; the splash
/// takes over before then).
pub const FILM_FRAMES: usize = 527;
/// The film crossfades into the splash (`splash.rs`) drawn natively once only the laptop's screen,
/// which shows the same composition, is visible; the splash's animation starts at the end of this.
pub const SPLASH_IN: (f32, f32) = (8.21667, 8.61667);
/// Picture fades up from black over this time.
pub const FADE_IN_END: f32 = 0.8;
/// The skip hint's letters fade in over this time, on their chip that is there from the start of
/// it (while the picture is still nearly black); out with everything at the end.
pub const HINT_IN: (f32, f32) = (0.2, 0.9);
/// Fade into the app's main screen.
pub const FADE_OUT: (f32, f32) = (11.11667, 11.71667);

pub fn total_frames() -> u64 {
    (TOTAL * FPS).round() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timings_are_ordered() {
        assert!(FADE_IN_END > 0.0 && FADE_IN_END < FADE_OUT.0);
        assert!(FADE_OUT.0 < FADE_OUT.1 && FADE_OUT.1 <= TOTAL);
        assert_eq!(total_frames(), 703);
        assert!(SLOW_FPS < FPS && (FPS / SLOW_FPS).fract() == 0.0, "slow frames are film frames");
        // the splash's streaks and sweep are over well before the fade
        assert!(SPLASH_IN.1 + crate::intro::splash::ACCENTS_END + 1.0 <= FADE_OUT.0);
        assert!(((FILM_FRAMES - 1) as f32 / FPS) >= SPLASH_IN.1, "the film runs until the splash has taken over");
    }
}
