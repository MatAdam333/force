//! Effects. We use tachyonfx's `EffectManager` for the short-lived effects that are convenient to
//! express as cell transforms (the startup list fade-in), applied to the rendered buffer each frame so
//! ratatui's diffing only pushes changed cells. The state-change "pulse" (a pure background tint), the
//! marker-column sweep and the usage-bar tween are small deterministic tweens implemented in `app`/`fx`
//! because tachyonfx 0.25 has no background-only fade and the bar needs a custom eased fill.

use ratatui::{buffer::Buffer, layout::Rect, style::Color};
use tachyonfx::{fx, Duration, EffectManager, Interpolation};

pub const BG: Color = Color::Rgb(0x26, 0x26, 0x26);

/// Animation intensity selected with `--animations`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum AnimMode {
    /// Everything: all animations plus the continuous working shimmer.
    Full,
    /// Everything except the continuous working shimmer.
    Subtle,
    /// No animations at all; every frame is drawn in its final state.
    Off,
}

impl AnimMode {
    /// Parse a `--animations` value; any unknown value falls back to `Full`.
    pub fn parse(s: &str) -> AnimMode {
        match s {
            "full" => AnimMode::Full,
            "subtle" => AnimMode::Subtle,
            "off" => AnimMode::Off,
            _ => AnimMode::Full,
        }
    }
    pub fn is_off(&self) -> bool {
        *self == AnimMode::Off
    }
    /// The working shimmer only runs in `full` mode.
    pub fn shimmer(&self) -> bool {
        *self == AnimMode::Full
    }
}

/// Own tweens owned/managed per render; entry points that let `app` drive them.
pub struct Effects {
    /// tachyonfx manager (used for the startup fade-in).
    manager: EffectManager<String>,
    /// Animation mode; every effect is a no-op in `Off`.
    pub mode: AnimMode,
    /// short background pulses keyed by session name (YOUR_TURN / DOWN).
    pub pulses: Vec<Pulse>,
    /// long attention pulses keyed by session name (NEEDS_YOU, ~10 s sinusoidal).
    pub attentions: Vec<Attention>,
    /// current-card edge-column top-to-bottom sweep (None when idle).
    pub edge_sweep: Option<Sweep>,
    /// usage-bar fill tween.
    pub usage_tween: Option<UsageTween>,
}

pub struct Pulse {
    pub name: String,
    pub color: Color,
    pub start_ms: f64,
    pub dur_ms: f64,
}

pub struct Attention {
    pub name: String,
    pub start_ms: f64,
    pub dur_ms: f64,
}

pub struct Sweep {
    pub start_ms: f64,
    pub dur_ms: f64,
}

pub struct UsageTween {
    pub from: [f64; 2],
    pub to: [f64; 2],
    pub start_ms: f64,
    pub dur_ms: f64,
}

impl Effects {
    pub fn new() -> Self {
        Self {
            manager: EffectManager::<String>::default(),
            mode: AnimMode::Full,
            pulses: Vec::new(),
            attentions: Vec::new(),
            edge_sweep: None,
            usage_tween: None,
        }
    }

    pub fn set_mode(&mut self, mode: AnimMode) {
        self.mode = mode;
        if mode.is_off() {
            // `off` draws every frame in its final state: clear any lingering effects.
            self.manager = EffectManager::<String>::default();
        }
    }

    /// Startup: the whole list fades in from the background colour (~300 ms). No-op when `off`.
    pub fn start_fade(&mut self, area: Rect) {
        if self.mode.is_off() {
            return;
        }
        self.manager.add_effect(
            fx::fade_from_fg(BG, (300, Interpolation::Linear)).with_area(area),
        );
    }

    /// Add a short background pulse for a state change (YOUR_TURN / DOWN).
    pub fn pulse(&mut self, name: String, color: Color, now_ms: f64) {
        if self.mode.is_off() {
            return;
        }
        self.pulses.retain(|p| p.name != name);
        self.pulses.push(Pulse { name, color, start_ms: now_ms, dur_ms: 450.0 });
    }

    /// Add a long attention pulse for a session entering NEEDS_YOU (~10 s).
    pub fn attention(&mut self, name: String, now_ms: f64) {
        if self.mode.is_off() {
            return;
        }
        self.attentions.retain(|a| a.name != name);
        self.attentions.push(Attention { name, start_ms: now_ms, dur_ms: 10_000.0 });
    }

    pub fn trigger_edge_sweep(&mut self, now_ms: f64) {
        if self.mode.is_off() {
            return;
        }
        self.edge_sweep = Some(Sweep { start_ms: now_ms, dur_ms: 250.0 });
    }

    pub fn start_usage_tween(&mut self, from: [f64; 2], to: [f64; 2], now_ms: f64) {
        if self.mode.is_off() {
            return;
        }
        self.usage_tween = Some(UsageTween { from, to, start_ms: now_ms, dur_ms: 400.0 });
    }

    /// Advance/drop finished effect bookkeeping; returns true while anything is still animating.
    pub fn tick(&mut self, now_ms: f64) {
        self.pulses.retain(|p| (now_ms - p.start_ms) < p.dur_ms);
        self.attentions.retain(|a| (now_ms - a.start_ms) < a.dur_ms);
        if let Some(s) = self.edge_sweep.as_ref() {
            if (now_ms - s.start_ms) >= s.dur_ms {
                self.edge_sweep = None;
            }
        }
        if let Some(u) = self.usage_tween.as_ref() {
            if (now_ms - u.start_ms) >= u.dur_ms {
                self.usage_tween = None;
            }
        }
    }

    /// Snappy effects that want ~30 fps frames (fade, short pulses, sweep, tween).
    pub fn snappy_running(&self) -> bool {
        self.manager.is_running()
            || !self.pulses.is_empty()
            || self.edge_sweep.is_some()
            || self.usage_tween.is_some()
    }

    /// Slow attention pulses that only need ~12 fps frames.
    pub fn attention_running(&self) -> bool {
        !self.attentions.is_empty()
    }

    /// Whether any effect (tachyonfx or own) is still running -> drive frames.
    pub fn running(&self) -> bool {
        self.snappy_running() || self.attention_running()
    }

    /// Run tachyonfx effects on the freshly-drawn buffer (startup fade).
    pub fn process(&mut self, elapsed_ms: u32, buf: &mut Buffer, area: Rect) {
        self.manager
            .process_effects(Duration::from_millis(elapsed_ms), buf, area);
    }

    /// A clamped linear ease for own tweens.
    pub fn ease(t: f64) -> f64 {
        t.clamp(0.0, 1.0)
    }
}

impl Default for Effects {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The same `--animations` parser is used by BOTH binaries (force-sidebar and force-header):
    // full/subtle/off are recognised and anything unknown falls back to full (never exits).
    #[test]
    fn animations_flag_parsing_for_both_binaries() {
        assert_eq!(AnimMode::parse("full"), AnimMode::Full);
        assert_eq!(AnimMode::parse("subtle"), AnimMode::Subtle);
        assert_eq!(AnimMode::parse("off"), AnimMode::Off);
        // unknown / missing value -> full, never an error
        assert_eq!(AnimMode::parse("garble"), AnimMode::Full);
        assert_eq!(AnimMode::parse(""), AnimMode::Full);
        assert_eq!(AnimMode::parse("FULL"), AnimMode::Full);
    }

    #[test]
    fn shimmer_is_full_only() {
        assert!(AnimMode::Full.shimmer());
        assert!(!AnimMode::Subtle.shimmer());
        assert!(!AnimMode::Off.shimmer());
        assert!(AnimMode::Off.is_off());
        assert!(!AnimMode::Subtle.is_off());
    }
}
