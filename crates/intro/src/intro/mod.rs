//! Cinematic startup animation: a man in a suit walks in, the camera orbits, he sits down in an
//! armchair. The scene is a film pre-rendered in Blender (`blender/`) from CC0 assets and played
//! here, scaled to the terminal.
//!
//! Layout (one concern per file):
//! - `film.rs`     the embedded film: format, decoding
//! - `grade.rs`    film frame -> terminal pixels: scaling, letterbox, fades, vignette, grain
//! - `splash.rs`   the brand splash on the laptop's screen, drawn crisp once the film has zoomed in
//! - `timing.rs`   playback rate, length, fades (shot timing lives in `blender/scene.py`)
//! - `math.rs`     small vector type, easing, hashing
//! - `output.rs`   terminal encoding (half blocks, 256 colours, ASCII), diffed output
//! - `term.rs`     terminal setup/restore and the frame loop
//! - `png.rs`      PNG stills for checking frames without a terminal

pub mod film;
pub mod grade;
pub mod math;
pub mod output;
pub mod png;
pub mod splash;
pub mod term;
pub mod timing;

use grade::{Glyph, Image, Settings};
use math::{v3, V3};
use output::ColorMode;

/// Shown in the bottom-right corner while it plays (any key skips): the words, then the key hint.
pub const SKIP_HINT: &str = "skip intro · any key";
const SKIP_WORDS: usize = 14;
const HINT: V3 = v3(0x8a as f32 / 255.0, 0x8a as f32 / 255.0, 0x8a as f32 / 255.0);
const HINT_KEY: V3 = v3(0x70 as f32 / 255.0, 0x70 as f32 / 255.0, 0x70 as f32 / 255.0);

/// Same values as force's `animations` setting (and the `--animations` flag of its Rust panes).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AnimMode {
    /// Everything, including animated film grain.
    Full,
    /// No continuous effects: static grain.
    Subtle,
    /// The intro does not play.
    Off,
}

impl AnimMode {
    /// Unknown values fall back to full, like the force panes.
    pub fn parse(s: &str) -> AnimMode {
        match s {
            "subtle" => AnimMode::Subtle,
            "off" => AnimMode::Off,
            _ => AnimMode::Full,
        }
    }
}

/// What the last frame fades into.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FadeTo {
    Rgb(u8, u8, u8),
    /// The terminal's own default background.
    Default,
}

impl FadeTo {
    /// `default`, or six hex digits with an optional leading `#`.
    pub fn parse(s: &str) -> Option<FadeTo> {
        if s == "default" {
            return Some(FadeTo::Default);
        }
        let h = s.strip_prefix('#').unwrap_or(s);
        if h.len() != 6 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        let byte = |i: usize| u8::from_str_radix(&h[i..i + 2], 16).ok();
        Some(FadeTo::Rgb(byte(0)?, byte(2)?, byte(4)?))
    }

    fn display(self) -> V3 {
        match self {
            FadeTo::Rgb(r, g, b) => v3(r as f32 / 255.0, g as f32 / 255.0, b as f32 / 255.0),
            FadeTo::Default => V3::ZERO,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Options {
    pub animations: AnimMode,
    /// `None` = detect from the environment.
    pub color: Option<ColorMode>,
    pub fade_to: FadeTo,
    /// Leave the alternate screen up, painted in `fade_to`, after a normal end or a skip, for a
    /// program that takes the screen over right away (no flash of the shell in between).
    pub handoff: bool,
    /// Terminal cell height / width; `None` = measured from the terminal's pixel size where it
    /// reports one (most do, also over ssh), else 2.0.
    pub cell_aspect: Option<f32>,
    /// Development: hold this quality level (`term::QUALITY`) instead of adapting.
    pub quality: Option<usize>,
    /// Development: write one line per frame sent here ("t level fps bytes").
    pub trace: Option<std::path::PathBuf>,
    pub min_cols: u16,
    pub min_rows: u16,
}

impl Default for Options {
    fn default() -> Options {
        Options {
            animations: AnimMode::Full,
            color: None,
            fade_to: FadeTo::Default,
            handoff: false,
            cell_aspect: None,
            quality: None,
            trace: None,
            min_cols: 80,
            min_rows: 24,
        }
    }
}

impl Options {
    /// The cell aspect in use (2.0 where it is neither given nor measured).
    pub fn cell_aspect(&self) -> f32 {
        self.cell_aspect.unwrap_or(2.0)
    }
}

/// How a run ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Completed,
    Skipped,
    /// Ctrl+C or a signal: the process should exit with this code.
    Interrupted(i32),
    /// Not played at all, nothing on the terminal was touched (the reason is for logs).
    NotShown(&'static str),
}

/// How a run went, for `--verbose`.
#[derive(Clone, Debug)]
pub struct Report {
    pub outcome: Outcome,
    /// Wall-clock time from the first frame to the end.
    pub seconds: f32,
    /// Frames written.
    pub frames: usize,
    /// When it fell back to the slow rate (the terminal couldn't keep up), seconds from the start.
    pub slow_from: Option<f32>,
    /// The terminal answered the flow-control queries.
    pub flow_control: bool,
    /// Cell height / width it played with, and whether it was measured.
    pub cell_aspect: f32,
    pub cell_aspect_measured: bool,
    /// The quality level it ended on (`term::QUALITY`), and when it got there.
    pub quality: usize,
    pub quality_from: Option<f32>,
    /// Back to the full rate (when the native splash took over), seconds from the start.
    pub slow_until: Option<f32>,
    /// Frames sent at each quality level.
    pub level_frames: Vec<usize>,
    /// The lowest frame rate it stepped down to (from `slow_from`).
    pub min_fps: u32,
}

impl From<Outcome> for Report {
    fn from(outcome: Outcome) -> Report {
        Report {
            outcome,
            seconds: 0.0,
            frames: 0,
            slow_from: None,
            flow_control: false,
            cell_aspect: 2.0,
            cell_aspect_measured: false,
            quality: 0,
            quality_from: None,
            slow_until: None,
            level_frames: Vec::new(),
            min_fps: 60,
        }
    }
}

/// Plays the animation on the terminal and restores it afterwards.
pub fn run(opts: &Options) -> std::io::Result<Report> {
    term::play(opts)
}

/// Image pixel size for a terminal of `cols` x `rows` cells in `mode`.
pub fn image_size(cols: usize, rows: usize, mode: ColorMode) -> (usize, usize) {
    (cols, rows * mode.pixels_per_cell())
}

/// The picture at time `t` for a `cols` x `rows` terminal. `still_grain` holds the film grain still
/// (as in `subtle`): a terminal that can't keep up then gets far fewer changed cells per frame.
pub fn render_frame(
    t: f32,
    still_grain: bool,
    cols: usize,
    rows: usize,
    mode: ColorMode,
    opts: &Options,
) -> Image {
    let film = film::Film::embedded();
    let (w, h) = image_size(cols, rows, mode);
    let settings = Settings {
        pixel_aspect: 1.0 / opts.cell_aspect() * mode.pixels_per_cell() as f32,
        // film grain changes at 10 Hz in full mode; static otherwise (saves output bytes)
        grain_seed: if opts.animations == AnimMode::Full && !still_grain {
            Some((t.max(0.0) * 10.0) as u32)
        } else {
            None
        },
        fade_to: opts.fade_to.display(),
    };
    let splash_w = splash::weight(t);
    let mut img = if splash_w < 1.0 {
        let index = ((t * film.fps as f32).round().max(0.0) as usize).min(film.frame_count() - 1);
        let src = film::embedded_frame(index);
        grade::compose(&src, film.w, film.h, t, w, h, &settings)
    } else {
        Image::new(w, h)
    };
    // once only the laptop's screen is visible, the splash takes over from the film
    let anim = (opts.animations == AnimMode::Full).then_some(splash::Anim {
        since: t - timing::SPLASH_IN.1,
    });
    let ppc = mode.pixels_per_cell();
    splash::draw(&mut img, ppc, settings.pixel_aspect, anim, splash_w);
    let hint_w = math::smoothstep(timing::HINT_IN.0, timing::HINT_IN.1, t);
    skip_hint(&mut img, ppc, settings.pixel_aspect, hint_w);
    grade::finish(
        &mut img,
        settings.fade_to,
        math::smoothstep(timing::FADE_OUT.0, timing::FADE_OUT.1, t),
    );
    img
}

/// Where the skip hint starts on a `cols` x `rows` terminal (half-block pixels `pixel_aspect` wide):
/// row and first column, inside the picture's bottom-right corner, 2 columns from its right edge
/// and 1 row above its bottom. `None` if the picture is too small for it.
pub fn hint_place(cols: usize, rows: usize, pixel_aspect: f32) -> Option<(usize, usize)> {
    let (px0, py0, pw, ph) = grade::picture_rect(cols, rows * 2, pixel_aspect, film::aspect());
    let n = SKIP_HINT.chars().count();
    // whole cells inside the picture: columns [first, end), rows [.., bottom)
    let (first, end) = (px0.ceil() as usize, ((px0 + pw).floor() as usize).min(cols));
    let bottom = (((py0 + ph) / 2.0).floor() as usize).min(rows);
    (end >= first + n + 4 && bottom >= 3).then(|| (bottom - 2, end - 2 - n))
}

/// The hint chip's background: the hint is text cells with their own background (padded by a
/// cell each side), drawn over the film without touching its pixels.
const HINT_CHIP: V3 = V3::ZERO;

/// The skip hint over the picture's bottom-right corner, with opacity `w` (its letters fade in
/// over the chip; the chip itself comes up while the picture is still black).
fn skip_hint(img: &mut Image, ppc: usize, pixel_aspect: f32, w: f32) {
    let (cols, rows) = (img.w, img.h / ppc);
    // place it on the half-block grid (as the splash does), whatever the pixels per cell
    let Some((r, c0)) = hint_place(cols, rows, pixel_aspect * 2.0 / ppc as f32) else { return };
    if w <= 0.0 {
        return;
    }
    let chars = std::iter::once(' ').chain(SKIP_HINT.chars()).chain(std::iter::once(' '));
    for (k, ch) in chars.enumerate() {
        let (c, i) = (c0 + k - 1, k.wrapping_sub(1));
        if c >= cols {
            continue;
        }
        let fg = if i < SKIP_WORDS { HINT } else { HINT_KEY };
        let fg = HINT_CHIP.lerp(fg, w);
        img.text.push((c, r, Glyph { ch, fg, bold: false, bg: Some(HINT_CHIP) }));
    }
}

/// RGB bytes as the terminal would show `img` in `mode` (quantised to the palette), with each
/// image pixel scaled to `scale` x `scale * pixel_h` output pixels.
pub fn preview_rgb(
    img: &Image,
    mode: ColorMode,
    scale: usize,
    pixel_h: usize,
) -> (usize, usize, Vec<u8>) {
    let (w, h) = (img.w * scale, img.h * scale * pixel_h);
    let mut out = vec![0u8; w * h * 3];
    for y in 0..img.h {
        for x in 0..img.w {
            let c = img.get(x, y);
            let rgb = match mode {
                ColorMode::Ansi256 => xterm_rgb(output::to_256(c, math::bayer4(x, y) - 0.5)),
                ColorMode::Ascii => {
                    let l = (c.luma() * 255.0) as u8;
                    [l, l, l]
                }
                ColorMode::TrueColor => {
                    let q = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;
                    [q(c.x), q(c.y), q(c.z)]
                }
            };
            for dy in 0..scale * pixel_h {
                let row = (y * scale * pixel_h + dy) * w;
                for dx in 0..scale {
                    let i = (row + x * scale + dx) * 3;
                    out[i..i + 3].copy_from_slice(&rgb);
                }
            }
        }
    }
    // characters, drawn with the splash's pixel font over the cell's background
    let ppc = mode.pixels_per_cell();
    let (cw, ch) = (scale, scale * pixel_h * ppc);
    for &(c, r, g) in &img.text {
        let rgb = |v: V3| match mode {
            ColorMode::Ansi256 => xterm_rgb(output::to_256(v, 0.0)),
            _ => {
                let q = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;
                [q(v.x), q(v.y), q(v.z)]
            }
        };
        let fg = rgb(g.fg);
        // a cell with its own background: paint it first
        if let Some(bg) = g.bg {
            let bg = rgb(bg);
            for py in 0..ch {
                for px in 0..cw {
                    let (x, y) = (c * cw + px, r * ch + py);
                    if x < w && y < h {
                        let i = (y * w + x) * 3;
                        out[i..i + 3].copy_from_slice(&bg);
                    }
                }
            }
        }
        for py in 0..ch {
            for px in 0..cw {
                let (x, y) = (c * cw + px, r * ch + py);
                if x >= w || y >= h {
                    continue;
                }
                let cover = splash::glyph_cover(g.ch, g.bold, (px as f32 + 0.5) / cw as f32, (py as f32 + 0.5) / ch as f32);
                if cover > 0.0 {
                    let i = (y * w + x) * 3;
                    for k in 0..3 {
                        out[i + k] = (out[i + k] as f32 * (1.0 - cover) + fg[k] as f32 * cover) as u8;
                    }
                }
            }
        }
    }
    (w, h, out)
}

/// The RGB value of an xterm-256 palette entry (16..255).
pub fn xterm_rgb(i: u8) -> [u8; 3] {
    const LEVELS: [u8; 6] = [0, 95, 135, 175, 215, 255];
    match i {
        16..=231 => {
            let n = i - 16;
            [
                LEVELS[(n / 36) as usize],
                LEVELS[(n / 6 % 6) as usize],
                LEVELS[(n % 6) as usize],
            ]
        }
        232..=255 => {
            let v = 8 + 10 * (i - 232);
            [v, v, v]
        }
        _ => [0, 0, 0],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_fade_targets() {
        assert_eq!(FadeTo::parse("262626"), Some(FadeTo::Rgb(0x26, 0x26, 0x26)));
        assert_eq!(
            FadeTo::parse("#d97757"),
            Some(FadeTo::Rgb(0xd9, 0x77, 0x57))
        );
        assert_eq!(FadeTo::parse("default"), Some(FadeTo::Default));
        for bad in ["", "12345", "1234567", "zzzzzz", "#12"] {
            assert_eq!(FadeTo::parse(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn anim_mode_parses_like_cockpit() {
        assert_eq!(AnimMode::parse("off"), AnimMode::Off);
        assert_eq!(AnimMode::parse("subtle"), AnimMode::Subtle);
        assert_eq!(AnimMode::parse("bogus"), AnimMode::Full);
    }

    #[test]
    fn palette_round_trip() {
        assert_eq!(xterm_rgb(16), [0, 0, 0]);
        assert_eq!(xterm_rgb(231), [255, 255, 255]);
        assert_eq!(xterm_rgb(52), [95, 0, 0]);
        assert_eq!(xterm_rgb(232), [8, 8, 8]);
    }

    #[test]
    fn frames_render_at_both_reference_sizes() {
        let opts = Options::default();
        for (c, r) in [(120, 40), (80, 24)] {
            let img = render_frame(6.0, false, c, r, ColorMode::TrueColor, &opts);
            assert_eq!((img.w, img.h), (c, r * 2));
            let ascii = render_frame(6.0, false, c, r, ColorMode::Ascii, &opts);
            assert_eq!((ascii.w, ascii.h), (c, r));
        }
    }

    #[test]
    fn skip_hint_sits_inside_the_picture_clear_of_the_splash() {
        let opts = Options::default();
        let n = SKIP_HINT.chars().count();
        for (c, r) in [(80, 24), (120, 40), (191, 47), (300, 50), (100, 60)] {
            let (hr, hc) = hint_place(c, r, 1.0).unwrap();
            let (px0, py0, pw, ph) = grade::picture_rect(c, r * 2, 1.0, film::aspect());
            // inside the picture: 2 columns from its right edge, 1 row above its bottom
            assert!(hc as f32 >= px0 && (hr * 2) as f32 >= py0, "{c}x{r}");
            assert_eq!(hc + n + 2, ((px0 + pw).floor() as usize).min(c), "{c}x{r}: right margin");
            assert_eq!(hr + 2, (((py0 + ph) / 2.0).floor() as usize).min(r), "{c}x{r}: bottom margin");
            // clear of the splash (below its tag line)
            let (_, last) = splash::layout(c, r, 1.0).rows();
            assert!(hr > last + 1, "{c}x{r}: hint row {hr} vs the splash's last row {last}");
            for mode in [ColorMode::TrueColor, ColorMode::Ansi256, ColorMode::Ascii] {
                let img = render_frame(12.0, false, c, r, mode, &opts);
                let hint: Vec<_> = img.text.iter().filter(|(col, row, _)| *row == hr && *col + 1 >= hc).collect();
                let text: String = hint.iter().map(|(_, _, g)| g.ch).collect();
                assert_eq!(text, format!(" {SKIP_HINT} "), "{c}x{r} {mode:?}: the chip, padded");
                assert_eq!(hint[0].0 + 1, hc);
            }
        }
        // a native overlay: text cells on their own background, the film's pixels untouched, and
        // legible on any frame
        let (c, r) = (120, 40);
        let (hr, hc) = hint_place(c, r, 1.0).unwrap();
        let mut img = Image::new(c, r * 2);
        for (i, p) in img.px.iter_mut().enumerate() {
            *p = V3::splat((i % 7) as f32 / 7.0);
        }
        let before = img.px.clone();
        skip_hint(&mut img, 2, 1.0, 1.0);
        assert_eq!(img.px, before, "the film's pixels are not touched");
        assert_eq!(img.text.len(), SKIP_HINT.chars().count() + 2);
        for &(col, row, g) in &img.text {
            assert_eq!((row, g.bg), (hr, Some(HINT_CHIP)));
            assert!(col + 1 >= hc);
            if g.ch != ' ' {
                assert!(g.fg.luma() - HINT_CHIP.luma() > 0.4, "{:?}", g.fg);
            }
        }
        // not there before it fades in
        let early = render_frame(0.1, false, 120, 40, ColorMode::TrueColor, &opts);
        assert!(timing::HINT_IN.0 > 0.1);
        assert!(early.text.is_empty());
    }

    #[test]
    fn splash_is_flat_and_native_once_it_has_taken_over() {
        let opts = Options::default();
        for (c, r) in [(120, 40), (191, 47)] {
            for t in [timing::SPLASH_IN.1, 12.0, timing::FADE_OUT.0] {
                let img = render_frame(t, false, c, r, ColorMode::TrueColor, &opts);
                let (first, last) = splash::layout(c, r, 1.0).rows();
                // outside the composition's rows: exactly the flat background, every frame
                for y in 0..img.h {
                    for x in 0..img.w {
                        let inside = y / 2 >= first && y / 2 <= last;
                        if !inside {
                            assert_eq!(img.get(x, y), splash::BG, "{c}x{r} t={t}: ({x},{y})");
                        }
                    }
                }
            }
        }
    }
}
