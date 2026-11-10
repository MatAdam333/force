//! The brand splash, "Underline, black" (the owner's pick, 2026-10-16): on pure black, the italic
//! `FORCE` wordmark in Archivo Black (a coral to peach gradient, half-block pixels), a thin
//! underline overhanging it, and under that the name, `The code cockpit`, as text cells.
//!
//! The film's laptop screen shows it at rest from the lid opening on ([`texture`]); once only the
//! screen is visible, the film crossfades into the same composition drawn natively by the terminal,
//! and then it comes alive: speed streaks rush into the word from the left and fade, a light
//! sweeps over the word, a soft light runs along the underline, and a working shimmer passes
//! over the name.
//!
//! It is the reference renderer's design (`make_d("underline")`), re-implemented: everything is
//! placed and composited on its 1200 px wide canvas of square pixels (the reference terminal is
//! 120 x 40 cells of 10 x 21 px), only over the band of rows the composition covers, then
//! box-averaged onto the terminal's half-block pixels, whatever its size and cell shape. The
//! wordmark itself is a mask baked from the font (`tools/force/bake.py` -> `assets/force.mask`).

use std::sync::OnceLock;

use super::grade::{Glyph, Image};
use super::math::{smoothstep, V3};
use super::timing as T;

/// The background: flat black.
pub const BG: V3 = rgb(0x000000);
const CORAL: V3 = rgb(0xd97757);
const PEACH: V3 = rgb(0xf6c2a4);
const UNDERLINE: V3 = rgb(0xa8553a);
/// The light running along the underline, and the one sweeping over the word.
const UNDERLINE_LIGHT: V3 = rgb(0xffd9c4);
const SWEEP: V3 = rgb(0xfff1e6);
const TAG_GREY: V3 = rgb(0x9a9a9a);
/// The shimmer's highlight on the name.
const TAG_SHIMMER: V3 = rgb(0xf2e6dc);

/// The line under the wordmark (owner 2026-11-10: the name Force goes by; the earlier chip and tagline are
/// retired).
pub const TAG: &str = "The code cockpit";

/// The design canvas is this wide, in its own (square) pixels, whatever the terminal's width.
const CANVAS_W: f32 = 1200.0;
/// The underline: this far under the word's box, this thick (rows), overhanging it by this much.
const UNDERLINE_GAP: f32 = 22.0;
const UNDERLINE_ROWS: i32 = 5;
const UNDERLINE_OVERHANG: f32 = 40.0;
/// Empty cell rows between the underline and the tag line, plus one.
const TAG_GAP_ROWS: f32 = 4.0;

// The animation, in seconds of the splash's own timeline (0 = the crossfade's end). Each streak's
// head eases in over STREAK_RUSH (after its own small delay); then they all fade.
const STREAK_RUSH: f32 = 0.45;
const STREAK_FADE: (f32, f32) = (0.6, 1.2);
const WORD_SWEEP: (f32, f32) = (0.3, 0.95);
const LIGHT_FROM: f32 = 0.2;
const LIGHT_SPEED: f32 = 520.0;
/// The shimmer passes over the name every this many seconds.
const SHIMMER_EVERY: f32 = 1.4;
/// After this the streaks and the sweep are over: the composition is at rest again (the
/// underline's light and the shimmer go on).
pub const ACCENTS_END: f32 = 1.2;

/// The reference terminal the film's laptop screen is laid out for: a full-screen 16:9 kitty
/// (191 x 47 cells of about 10 x 23 px). It must be at least as wide as the film (16:10): then
/// the composition spans the same share of the picture as on any other terminal that wide, and
/// the film crossfades into the native splash in register.
pub const REFERENCE: (usize, usize) = (191, 47);
/// Its half-block pixels' width / height (cells 2.29 times as tall as wide).
pub const REFERENCE_PA: f32 = 2.0 / 2.29;

const fn rgb(c: u32) -> V3 {
    V3 {
        x: ((c >> 16) & 0xff) as f32 / 255.0,
        y: ((c >> 8) & 0xff) as f32 / 255.0,
        z: (c & 0xff) as f32 / 255.0,
    }
}

// ---- the baked wordmark --------------------------------------------------------------------

/// One speed streak: its row and where its head comes to rest, relative to the word's box.
struct Streak {
    dy: i32,
    dend: i32,
    length: f32,
    delay: f32,
}

/// The `FORCE` mask (coverage 0..255, cropped to its box) and where the box sits on the
/// reference canvas, with the animation's streaks.
struct Mask {
    w: usize,
    h: usize,
    x0: i32,
    y0: i32,
    streaks: Vec<Streak>,
    px: Vec<u8>,
}

impl Mask {
    fn parse(d: &[u8]) -> Result<Mask, String> {
        let u16_at = |i: usize| d.get(i..i + 2).map(|b| u16::from_le_bytes([b[0], b[1]]));
        let i16_at = |i: usize| u16_at(i).map(|v| v as i16 as i32);
        if d.get(..4) != Some(b"FMSK") {
            return Err("not a force mask".into());
        }
        let bad = || "truncated force mask".to_string();
        let (w, h) = (u16_at(4).ok_or_else(bad)? as usize, u16_at(6).ok_or_else(bad)? as usize);
        let (x0, y0) = (i16_at(8).ok_or_else(bad)?, i16_at(10).ok_or_else(bad)?);
        let n = *d.get(12).ok_or_else(bad)? as usize;
        let mut streaks = Vec::with_capacity(n);
        for k in 0..n {
            let o = 13 + k * 10;
            let b = d.get(o..o + 10).ok_or_else(bad)?;
            streaks.push(Streak {
                dy: i16::from_le_bytes([b[0], b[1]]) as i32,
                dend: i16::from_le_bytes([b[2], b[3]]) as i32,
                length: u16::from_le_bytes([b[4], b[5]]) as f32,
                delay: f32::from_le_bytes([b[6], b[7], b[8], b[9]]),
            });
        }
        let px = miniz_oxide::inflate::decompress_to_vec_zlib(&d[13 + n * 10..])
            .map_err(|e| format!("force mask: {e:?}"))?;
        if w == 0 || h == 0 || px.len() != w * h {
            return Err(format!("force mask: {} bytes for {w}x{h}", px.len()));
        }
        Ok(Mask { w, h, x0, y0, streaks, px })
    }

    /// Coverage (0..255) at mask pixel (`u`, `v`); 0 outside.
    fn at(&self, u: i32, v: i32) -> u8 {
        if u < 0 || v < 0 || u >= self.w as i32 || v >= self.h as i32 {
            0
        } else {
            self.px[v as usize * self.w + u as usize]
        }
    }
}

fn mask() -> &'static Mask {
    static DATA: &[u8] = include_bytes!("../../assets/force.mask");
    static M: OnceLock<Mask> = OnceLock::new();
    M.get_or_init(|| Mask::parse(DATA).expect("embedded force mask is valid"))
}

// ---- layout ------------------------------------------------------------------------------

/// Where things sit for a terminal: canvas pixels per half-block pixel, the word's box on the
/// canvas (x0, y0, x1, y1; right and bottom exclusive), the underline's top row, and the tag line
/// in cells (row, first column of the name).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Layout {
    /// The terminal column where canvas x = 0 sits (negative when the canvas is wider than the
    /// terminal: a terminal narrower than the film, which crops the film's sides).
    pub x0: f32,
    pub kx: f32,
    pub ky: f32,
    pub word: (i32, i32, i32, i32),
    pub line_y: i32,
    pub tag: (usize, usize),
}

impl Layout {
    /// Canvas height of one cell row.
    fn cell_h(&self) -> f32 {
        2.0 * self.ky
    }

    /// The cell rows the composition covers, first and last (the tag line).
    pub fn rows(&self) -> (usize, usize) {
        ((self.word.1 as f32 / self.cell_h()).floor().max(0.0) as usize, self.tag.0)
    }

    /// The canvas rows that have pixels: from the word's top to the underline's bottom.
    fn band(&self) -> (i32, i32) {
        (self.word.1, self.line_y + UNDERLINE_ROWS - 1)
    }

    /// The underline's columns when fully drawn (inclusive, as the reference's rectangle).
    fn underline_span(&self) -> (i32, i32) {
        let (x0, _, x1, _) = self.word;
        (x0 - UNDERLINE_OVERHANG as i32, x1 + UNDERLINE_OVERHANG as i32)
    }
}

/// The layout for a `cols` x `rows` terminal whose half-block pixels are `pixel_aspect` wide per
/// unit height (as in `grade::Settings`, for the half-block grid).
pub fn layout(cols: usize, rows: usize, pixel_aspect: f32) -> Layout {
    layout_for(cols, rows, pixel_aspect, super::film::aspect())
}

/// The layout for a film of `aspect` (width / height): the canvas spans the picture's width, as on
/// the film's laptop screen, so the crossfade is in register on any terminal. On a terminal at
/// least as wide as the film that is the terminal's width; on a narrower one the film is cropped
/// at the sides and the canvas is wider than the terminal (the composition as large as in the
/// film, no jump in size when the film hands over), but never so wide that the underline would
/// not fit with 2 columns to spare.
pub fn layout_for(cols: usize, rows: usize, pixel_aspect: f32, aspect: f32) -> Layout {
    let m = mask();
    let n = cols.max(1) as f32;
    let (_, _, pic_w, _) = super::grade::picture_rect(cols.max(1), rows.max(1) * 2, pixel_aspect, aspect);
    // the underline (the widest part) as a share of the canvas
    let share = (m.w as f32 + 2.0 * UNDERLINE_OVERHANG) / CANVAS_W;
    let span = pic_w.min((n - 4.0).max(1.0) / share).max(n);
    let x_off = (n - span) / 2.0;
    let kx = CANVAS_W / span;
    let ky = kx / pixel_aspect;
    let cell_h = 2.0 * ky;
    let height = rows as f32 * cell_h;
    let (x0, x1) = (m.x0 as f32, (m.x0 + m.w as i32) as f32);
    let (y0, y1) = (m.y0 as f32, (m.y0 + m.h as i32) as f32);
    // the word centred on its own box; the group (word to tag line) centred vertically, the word
    // on an even canvas row (as the reference does it)
    let dx = (CANVAS_W / 2.0 - (x0 + x1) / 2.0).trunc();
    let tag_row = |y1: f32| ((y1 + UNDERLINE_GAP + UNDERLINE_ROWS as f32 - 1.0) / cell_h).floor() + TAG_GAP_ROWS;
    let (top, bottom) = (y0, (tag_row(y1) + 1.0) * cell_h);
    let mut dy = (height / 2.0 - (top + bottom) / 2.0).trunc();
    dy -= (y0 + dy).rem_euclid(2.0);
    let word = ((x0 + dx) as i32, (y0 + dy) as i32, (x1 + dx) as i32, (y1 + dy) as i32);
    let line_y = word.3 + UNDERLINE_GAP as i32;
    let row = tag_row(word.3 as f32).max(0.0) as usize;
    let col = (cols / 2).saturating_sub(TAG.chars().count() / 2);
    Layout { x0: x_off, kx, ky, word, line_y, tag: (row, col) }
}

// ---- the build-in ------------------------------------------------------------------------

/// What is alive at a moment: `None` for the still final composition (`animations = subtle`).
#[derive(Clone, Copy, Debug)]
pub struct Anim {
    /// Seconds since the splash took over from the film.
    pub since: f32,
}

fn seg(t: f32, (t0, t1): (f32, f32)) -> f32 {
    ((t - t0) / (t1 - t0)).clamp(0.0, 1.0)
}

fn ease_out(p: f32) -> f32 {
    1.0 - (1.0 - p).powi(3)
}

fn ease_in_out(p: f32) -> f32 {
    p * p * (3.0 - 2.0 * p)
}

/// The composition's state at one moment.
struct State {
    /// The underline's light's centre.
    light: Option<f32>,
    /// Each streak's head (canvas x), once it has started, and the streaks' opacity.
    heads: Vec<Option<f32>>,
    streaks: f32,
    /// The sweep's position over the word (canvas x at the top).
    sweep: Option<f32>,
    /// Seconds into the name's shimmer.
    shimmer: Option<f32>,
}

fn state(l: &Layout, anim: Option<Anim>) -> State {
    let m = mask();
    let (x0, _, x1, _) = l.word;
    let (ua, ub) = l.underline_span();
    // at rest (the still, and the moments before the animation starts): nothing moves
    let mut st = State { light: None, heads: vec![None; m.streaks.len()], streaks: 0.0, sweep: None, shimmer: None };
    let Some(Anim { since: t }) = anim else { return st };
    if t < 0.0 {
        return st;
    }
    st.light = (t >= LIGHT_FROM)
        .then(|| ua as f32 + ((t - LIGHT_FROM) * LIGHT_SPEED).rem_euclid((ub - ua) as f32 + 300.0) - 150.0);
    st.streaks = 1.0 - seg(t, STREAK_FADE);
    if st.streaks > 0.0 {
        for (h, s) in st.heads.iter_mut().zip(&m.streaks) {
            let hf = ease_out(seg(t, (s.delay, s.delay + STREAK_RUSH)));
            if hf > 0.0 {
                let end = (x0 + s.dend) as f32;
                *h = Some(-s.length + (end + s.length) * hf);
            }
        }
    }
    if (WORD_SWEEP.0..=WORD_SWEEP.1).contains(&t) {
        st.sweep = Some(x0 as f32 - 150.0 + (x1 - x0 + 450) as f32 * ease_in_out(seg(t, WORD_SWEEP)));
    }
    st.shimmer = Some(t);
    st
}

// ---- compositing -------------------------------------------------------------------------

/// Error function (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7).
fn erf(x: f32) -> f32 {
    let t = 1.0 / (1.0 + 0.327_591_1 * x.abs());
    let y = 1.0
        - (((((1.061_405_4 * t - 1.453_152_1) * t) + 1.421_413_8) * t - 0.284_496_74) * t + 0.254_829_6)
            * t
            * (-x * x).exp();
    y.copysign(x)
}

/// Fraction of a gaussian (`sigma`) blurred unit box [`a`, `b`) at `x`.
fn blurred_box(x: f32, a: f32, b: f32, sigma: f32) -> f32 {
    let s = sigma * std::f32::consts::SQRT_2;
    0.5 * (erf((x - a) / s) - erf((x - b) / s))
}

/// The composition's pixels on the canvas band (`band()` rows, the canvas's full width).
fn composite(l: &Layout, st: &State) -> (i32, Vec<V3>) {
    let m = mask();
    let (j0, j1) = l.band();
    let (w, rows) = (CANVAS_W as usize, (j1 - j0 + 1).max(0) as usize);
    let mut px = vec![BG; w * rows];
    let (x0, y0, x1, _) = l.word;
    // the streaks, painted as the reference draws them (later strokes cover earlier ones)
    let mut streak = vec![0u8; w * rows];
    for (h, s) in st.heads.iter().zip(&m.streaks) {
        let Some(head) = *h else { continue };
        for k in 0..10 {
            let (xa, xb) = (head - s.length * (k + 1) as f32 / 10.0, head - s.length * k as f32 / 10.0);
            let v = (150.0 * (1.0 - k as f32 / 10.0).powi(2)) as u8;
            if v <= 2 {
                continue;
            }
            for y in [y0 + s.dy, y0 + s.dy + 1] {
                let r = y - j0;
                if r < 0 || r >= rows as i32 {
                    continue;
                }
                let (a, b) = ((xa.round() as i32).max(0), (xb.round() as i32).min(w as i32 - 1));
                for x in a..=b {
                    streak[r as usize * w + x as usize] = v;
                }
            }
        }
    }
    // the underline's light: a box blurred by a wide gaussian, three times as strong, on the line
    let (ua, ub) = l.underline_span();
    let light_x: Vec<f32> = match st.light {
        Some(pos) => (0..w).map(|i| blurred_box(i as f32 + 0.5, pos - 90.0, pos + 91.0, 40.0)).collect(),
        None => Vec::new(),
    };
    let ly = l.line_y as f32;
    let light_y: Vec<f32> =
        (0..UNDERLINE_ROWS).map(|r| blurred_box(ly + r as f32 + 0.5, ly, ly + UNDERLINE_ROWS as f32, 40.0)).collect();
    // the sweep: a slanted band blurred by a gaussian, over the word only
    let slope: f32 = 260.0 / 840.0;
    let cos = 1.0 / (1.0 + slope * slope).sqrt();
    let grad = |x: i32| {
        let t = ((x - x0) as f32 / (x1 - x0).max(1) as f32).clamp(0.0, 1.0);
        let q = |a: f32, b: f32| ((a * 255.0) + (b - a) * 255.0 * t).trunc() / 255.0;
        V3 { x: q(CORAL.x, PEACH.x), y: q(CORAL.y, PEACH.y), z: q(CORAL.z, PEACH.z) }
    };
    let over = |c: V3, col: V3, a: u8| if a == 0 { c } else { c.lerp(col, a as f32 / 255.0) };
    for r in 0..rows {
        let j = j0 + r as i32;
        for i in 0..w as i32 {
            let mut c = BG;
            {
                let lr = j - l.line_y;
                if (0..UNDERLINE_ROWS).contains(&lr) && i >= ua && i <= ub {
                    c = UNDERLINE;
                    if !light_x.is_empty() {
                        let v = (255.0 * light_x[i as usize] * light_y[lr as usize]) as u32;
                        c = over(c, UNDERLINE_LIGHT, (v * 3).min(255) as u8);
                    }
                }
            }
            c = over(c, CORAL, (streak[r * w + i as usize] as f32 * st.streaks) as u8);
            {
                let v = m.at(i - x0, j - y0);
                if v > 0 {
                    c = over(c, grad(i), v);
                    if let Some(pos) = st.sweep {
                        let centre = pos - 260.0 * (j as f32 + 0.5) / 840.0;
                        let d = (i as f32 + 0.5 - centre) * cos;
                        let band = (255.0 * blurred_box(d, -55.0 * cos, 55.0 * cos, 28.0)) as u32;
                        c = over(c, SWEEP, (band * v as u32 / 255) as u8);
                    }
                }
            }
            px[r * w + i as usize] = c;
        }
    }
    (j0, px)
}

/// Overlap weights of output pixels of `k` source pixels each onto a source of `n` pixels starting
/// at source position `from` (box filter, fractional at the edges).
fn box_weights(out: usize, k: f32, off: f32, from: i32, n: usize) -> Vec<Vec<(usize, f32)>> {
    (0..out)
        .map(|o| {
            let (a, b) = (o as f32 * k + off, (o + 1) as f32 * k + off);
            let (lo, hi) = (a.floor() as i32, b.ceil() as i32);
            (lo..hi)
                .filter_map(|s| {
                    let idx = s - from;
                    let wt = (b.min((s + 1) as f32) - a.max(s as f32)) / k;
                    (idx >= 0 && (idx as usize) < n && wt > 0.0).then_some((idx as usize, wt))
                })
                .collect()
        })
        .collect()
}

/// Draws the splash over `img` (a `cols` x `rows` terminal, 2 pixel rows per cell, or 1 for
/// ASCII) with opacity `w`; the tag line's cells go into `img.text`.
pub fn draw(img: &mut Image, ppc: usize, pixel_aspect: f32, anim: Option<Anim>, w: f32) {
    if w <= 0.0 {
        return;
    }
    let (cols, rows) = (img.w, img.h / ppc);
    let l = layout(cols, rows, pixel_aspect * 2.0 / ppc as f32);
    let st = state(&l, anim);
    let (j0, band) = composite(&l, &st);
    let band_rows = band.len() / CANVAS_W as usize;
    // onto the half-block grid, then (for one pixel per cell) the cell's two pixels averaged
    let wx = box_weights(cols, l.kx, -l.x0 * l.kx, 0, CANVAS_W as usize);
    let wy = box_weights(rows * 2, l.ky, 0.0, j0, band_rows);
    let mut grid = vec![BG; cols * rows * 2];
    for (y, wrow) in wy.iter().enumerate() {
        if wrow.is_empty() {
            continue;
        }
        for (x, wcol) in wx.iter().enumerate() {
            let mut acc = V3::ZERO;
            for &(r, a) in wrow {
                let line = &band[r * CANVAS_W as usize..];
                for &(i, b) in wcol {
                    acc += line[i] * (a * b);
                }
            }
            grid[y * cols + x] = acc;
        }
    }
    // The tag line is the film's preview font on the laptop screen but the terminal's own font
    // here, and on a terminal of another size a row or two off: crossfading them at once showed
    // both, a ghost. So the film's rows from under the underline to just below the tag line (flat
    // background in the composition) go in the first half of the crossfade, and the terminal's
    // name comes in the second.
    let (w_film_tag, w_name) = ((2.0 * w).min(1.0), (2.0 * w - 1.0).clamp(0.0, 1.0));
    let below_line = ((l.line_y + UNDERLINE_ROWS) as f32 / l.cell_h()).ceil().max(0.0) as usize;
    let tag_rows = below_line..=l.tag.0 + 1;
    for y in 0..img.h {
        let wy = if tag_rows.contains(&(y / ppc)) { w_film_tag } else { w };
        for x in 0..cols {
            let c = if ppc == 2 { grid[y * cols + x] } else { (grid[2 * y * cols + x] + grid[(2 * y + 1) * cols + x]) * 0.5 };
            let i = y * img.w + x;
            img.px[i] = if wy >= 1.0 { c } else { img.px[i].lerp(c, wy) };
        }
    }
    // the tag line: the name, with a working shimmer passing over it
    if l.tag.0 < rows {
        let n = TAG.chars().count() as f32;
        for (k, ch) in TAG.chars().enumerate() {
            let c = l.tag.1 + k;
            if ch == ' ' || c >= cols {
                continue;
            }
            let fg = match st.shimmer {
                Some(s) => {
                    let centre = -3.0 + (n + 6.0) * (s / SHIMMER_EVERY).fract();
                    let d = (k as f32 - centre).abs();
                    TAG_GREY.lerp(TAG_SHIMMER, (1.0 - d / 3.0).max(0.0).powi(2))
                }
                None => TAG_GREY,
            };
            img.text.push((c, l.tag.0, Glyph { ch, fg: BG.lerp(fg, w_name), bold: false, bg: None }));
        }
    }
}

// ---- previews, ASCII, the film -----------------------------------------------------------

/// How much of a cell `ch` covers at (`u`, `v`) in the cell (0..1, top-left origin), from a 5x7
/// pixel font (bold: one font pixel wider to the right). For previews: a terminal draws the
/// characters with its own font.
pub fn glyph_cover(ch: char, bold: bool, u: f32, v: f32) -> f32 {
    let plain = cover(ch, u, v);
    if bold && plain == 0.0 {
        return cover(ch, u - 0.7 / 5.0 * 0.6, v);
    }
    plain
}

fn cover(ch: char, u: f32, v: f32) -> f32 {
    let Some(rows) = font(ch) else { return 0.0 };
    let (gx, gy) = (((u - 0.15) / 0.7 * 5.0).floor(), ((v - 0.15) / 0.75 * 7.0).floor());
    if !(0.0..5.0).contains(&gx) || !(0.0..7.0).contains(&gy) {
        return 0.0;
    }
    f32::from(rows[gy as usize].as_bytes()[gx as usize] == b'#')
}

/// 5x7 bitmaps for the characters the splash and the skip hint use.
fn font(ch: char) -> Option<[&'static str; 7]> {
    Some(match ch {
        'T' => ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
        't' => ["..#..", "..#..", "#####", "..#..", "..#..", "..#.#", "...#."],
        's' => [".....", ".....", ".####", "#....", ".###.", "....#", "####."],
        'n' => [".....", ".....", "#.##.", "##..#", "#...#", "#...#", "#...#"],
        'p' => [".....", ".....", "####.", "#...#", "####.", "#....", "#...."],
        'y' => [".....", ".....", "#...#", "#...#", ".####", "....#", ".###."],
        'l' => [".##..", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
        'c' => [".....", ".....", ".###.", "#....", "#....", "#...#", ".###."],
        'a' => [".....", ".....", ".###.", "....#", ".####", "#...#", ".####"],
        'u' => [".....", ".....", "#...#", "#...#", "#...#", "#..##", ".##.#"],
        'd' => ["....#", "....#", ".##.#", "#..##", "#...#", "#...#", ".####"],
        'e' => [".....", ".....", ".###.", "#...#", "#####", "#....", ".###."],
        'o' => [".....", ".....", ".###.", "#...#", "#...#", "#...#", ".###."],
        'k' => ["#....", "#....", "#..#.", "#.#..", "##...", "#.#..", "#..#."],
        'h' => ["#....", "#....", "#.##.", "##..#", "#...#", "#...#", "#...#"],
        'i' => ["..#..", ".....", ".##..", "..#..", "..#..", "..#..", ".###."],
        'r' => [".....", ".....", "#.##.", "##..#", "#....", "#....", "#...."],
        '·' => [".....", ".....", ".....", "..#..", ".....", ".....", "....."],
        _ => return None,
    })
}

/// ASCII stand-ins for the splash's and the hint's characters.
pub fn ascii(ch: char) -> u8 {
    match ch {
        '·' => b'.',
        c if c.is_ascii() => c as u8,
        _ => b'*',
    }
}

/// The film's laptop-screen texture: the composition at rest as the reference terminal shows it,
/// `w` x `h` RGB bytes covering the whole display, for a film of `aspect` (width / height).
/// `frame` = where the final film frame (the picture) sits on the display, in display coordinates
/// 0..1 from its top-left: (left, top, right, bottom). The pixels come from the canvas itself
/// (smooth on the screen); the text from the preview font.
pub fn texture(w: usize, h: usize, frame: [f32; 4], aspect: f32) -> Vec<u8> {
    let (cols, rows) = REFERENCE;
    let l = layout(cols, rows, REFERENCE_PA);
    let (j0, band) = composite(&l, &state(&l, None));
    let band_rows = (band.len() / CANVAS_W as usize) as i32;
    let pic = super::grade::picture_rect(cols, rows * 2, REFERENCE_PA, aspect);
    let name_c = l.tag.1;
    let sample = |x: f32, y: f32| -> V3 {
        // a text cell of the tag line
        let (c, r) = (x.floor(), (y / 2.0).floor());
        if r >= 0.0 && r as usize == l.tag.0 && c >= 0.0 {
            let (c, u, v) = (c as usize, x - c, y / 2.0 - r);
            if (name_c..name_c + TAG.chars().count()).contains(&c) {
                let ch = TAG.chars().nth(c - name_c).unwrap_or(' ');
                return BG.lerp(TAG_GREY, glyph_cover(ch, false, u, v));
            }
        }
        // a canvas pixel
        let (i, j) = ((x * l.kx).floor() as i32, (y * l.ky).floor() as i32 - j0);
        if i < 0 || i >= CANVAS_W as i32 || j < 0 || j >= band_rows {
            return BG;
        }
        band[j as usize * CANVAS_W as usize + i as usize]
    };
    let q = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;
    let mut out = vec![0u8; w * h * 3];
    for ty in 0..h {
        for tx in 0..w {
            // 3x3 supersampling: crisp but not aliased at the texture's resolution
            let mut acc = V3::ZERO;
            for s in 0..9 {
                let (du, dv) = ((s % 3) as f32 / 3.0 + 1.0 / 6.0, (s / 3) as f32 / 3.0 + 1.0 / 6.0);
                let (u, v) = ((tx as f32 + du) / w as f32, (ty as f32 + dv) / h as f32);
                // display -> picture -> terminal pixels
                let fx = (u - frame[0]) / (frame[2] - frame[0]);
                let fy = (v - frame[1]) / (frame[3] - frame[1]);
                acc += sample(pic.0 + fx * pic.2, pic.1 + fy * pic.3);
            }
            let c = acc * (1.0 / 9.0);
            let i = (ty * w + tx) * 3;
            (out[i], out[i + 1], out[i + 2]) = (q(c.x), q(c.y), q(c.z));
        }
    }
    out
}

/// Opacity of the splash over the film at time `t`.
pub fn weight(t: f32) -> f32 {
    smoothstep(T::SPLASH_IN.0, T::SPLASH_IN.1, t)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The reference terminal's half-block pixel shape (cells of 10 x 21 px).
    const REF_PA: f32 = 2.0 / 2.1;

    fn frame(cols: usize, rows: usize, pa: f32, anim: Option<Anim>) -> Image {
        let mut img = Image::new(cols, rows * 2);
        draw(&mut img, 2, pa, anim, 1.0);
        img
    }

    #[test]
    fn mask_is_the_baked_wordmark() {
        let m = mask();
        assert_eq!((m.w, m.h, m.x0, m.y0, m.streaks.len()), (555, 103, 343, 298, 12));
        assert!(m.px.contains(&255) && m.px.iter().any(|&v| v > 0 && v < 255), "anti-aliased");
        assert!(Mask::parse(b"FMSK").is_err() && Mask::parse(b"nope").is_err());
    }

    #[test]
    fn layout_matches_the_reference_and_centres_the_group() {
        // the reference renderer's terminal shows the design across its own width (a film no wider
        // than it: aspect 1.0)
        let l = layout_for(120, 40, REF_PA, 1.0);
        assert_eq!(l.x0, 0.0);
        assert_eq!((l.word.0, l.word.2, l.word.3 - l.word.1), (323, 878, 103));
        assert_eq!(l.tag.1, 60 - TAG.chars().count() / 2, "the name centred, as in the reference");
        // the group (word top .. tag line bottom) centred within a cell
        let cell_h = 21.0;
        let centre = (l.word.1 as f32 + (l.tag.0 + 1) as f32 * cell_h) / 2.0;
        assert!((centre - 420.0).abs() <= cell_h, "centre {centre}");
        assert_eq!(l.tag.0 as f32, ((l.line_y + 4) as f32 / cell_h).floor() + 4.0, "three empty rows");
        for (c, r, pa) in [(80, 24, 1.0), (191, 47, 2.0 / 2.29), (300, 50, 1.0)] {
            let l = layout(c, r, pa);
            let (first, last) = l.rows();
            assert!(first >= 1 && last + 2 < r, "{c}x{r}: rows {first}..{last}");
            assert!(l.tag.1 + TAG.len() < c);
        }
    }

    #[test]
    fn at_rest_then_the_accents() {
        let (c, r) = (120, 40);
        let at = |t: f32| frame(c, r, REF_PA, Some(Anim { since: t }));
        let still = frame(c, r, REF_PA, None);
        let l = layout(c, r, REF_PA);
        let line_rows = |y: usize| {
            let (a, b) = (l.line_y as f32 / l.ky, (l.line_y + UNDERLINE_ROWS) as f32 / l.ky);
            y as f32 + 1.0 > a && (y as f32) < b
        };
        let same_except = |a: &Image, skip: &dyn Fn(usize) -> bool| {
            (0..a.h).all(|y| skip(y) || (0..a.w).all(|x| (a.get(x, y) - still.get(x, y)).length() < 1e-4))
        };
        // during the crossfade and at its end: exactly the still composition (the film's screen)
        for t in [-0.3, 0.0] {
            assert_eq!(at(t).px, still.px, "t={t}");
            assert_eq!(at(t).text, still.text, "t={t}");
        }
        // streaks rush in left of the word, then fade; the word itself never moves
        let lit_left = |img: &Image| {
            let (x0c, rows) = ((l.x0 + l.word.0 as f32 / l.kx) as usize, l.word.1 as f32 / l.ky..l.word.3 as f32 / l.ky);
            (rows.start as usize..rows.end as usize).flat_map(|y| (0..x0c).map(move |x| (x, y))).filter(|&(x, y)| img.get(x, y).x > 0.05).count()
        };
        assert_eq!(lit_left(&still), 0);
        assert!(lit_left(&at(0.4)) > 10, "streaks");
        assert!(lit_left(&at(0.9)) < lit_left(&at(0.6)), "fading");
        // the sweep brightens the word in the middle of its run
        let word_sum = |img: &Image| img.px.iter().map(|p| p.x + p.y + p.z).sum::<f32>();
        assert!(word_sum(&at(0.62)) > word_sum(&at(1.3)) + 1.0);
        // at rest again: only the underline's light moves (and the name shimmers)
        let rest = at(1.3);
        assert!(same_except(&rest, &line_rows));
        assert_ne!(at(1.5).px, at(1.6).px, "the light runs along the underline");
        let name = |img: &Image| img.text.iter().filter(|(_, _, g)| !g.bold).map(|g| g.2.fg).collect::<Vec<_>>();
        assert_ne!(name(&at(0.3)), name(&at(0.9)), "the shimmer moves");
        assert!(name(&still).iter().all(|&c| c == TAG_GREY));
        // the still: all of it, no accents
        assert_eq!(still.text.len(), TAG.chars().filter(|c| *c != ' ').count());
    }

    #[test]
    fn colours_are_the_specs() {
        let still = frame(120, 40, REF_PA, None);
        let l = layout(120, 40, REF_PA);
        // the name in grey, plain text
        assert!(still.text.iter().all(|(_, _, g)| g.fg == TAG_GREY && !g.bold && g.bg.is_none()));
        // the underline (thinner than a pixel: anti-aliased) in its colour; the word runs coral ->
        // peach left to right
        let row = ((l.line_y as f32 + UNDERLINE_ROWS as f32 / 2.0) / l.ky) as usize;
        let mid = still.get(60, row);
        let share = mid.x / UNDERLINE.x;
        assert!(share > 0.3 && (mid * (1.0 / share) - UNDERLINE).length() < 0.02, "{mid:?}");
        let wrow = ((l.word.1 + l.word.3) as f32 / 2.0 / l.ky) as usize;
        let lit: Vec<V3> = (0..120).map(|x| still.get(x, wrow)).filter(|p| p.x > 0.5).collect();
        assert!(lit.first().unwrap().y < lit.last().unwrap().y, "gradient towards peach");
        // everything outside the composition's rows is black
        let (first, last) = l.rows();
        for y in 0..still.h {
            if y / 2 < first || y / 2 > last {
                assert!((0..120).all(|x| still.get(x, y) == BG), "row {y}");
            }
        }
    }

    #[test]
    fn every_character_has_a_bitmap() {
        for ch in TAG.chars().chain(crate::intro::SKIP_HINT.chars()) {
            assert!(ch == ' ' || font(ch).is_some(), "{ch:?}");
        }
        let area = |bold: bool| {
            (0..100).map(|i| glyph_cover('c', bold, (i % 10) as f32 / 10.0 + 0.05, (i / 10) as f32 / 10.0 + 0.05)).sum::<f32>()
        };
        assert!(area(true) > area(false));
    }

    #[test]
    fn the_films_screen_shows_the_composition() {
        // the frame covering the whole display: the word and the name where the layout puts them
        let (w, h) = (1280, 800);
        let aspect = 1.6;
        let tex = texture(w, h, [0.0, 0.0, 1.0, 1.0], aspect);
        let px = |x: usize, y: usize| {
            let i = (y * w + x) * 3;
            V3 { x: tex[i] as f32 / 255.0, y: tex[i + 1] as f32 / 255.0, z: tex[i + 2] as f32 / 255.0 }
        };
        assert_eq!(px(2, 2), BG);
        let l = layout(REFERENCE.0, REFERENCE.1, REFERENCE_PA);
        let pic = crate::intro::grade::picture_rect(REFERENCE.0, REFERENCE.1 * 2, REFERENCE_PA, aspect);
        assert_eq!((pic.0, pic.2), (0.0, REFERENCE.0 as f32), "the reference is at least as wide as the film");
        let to_tex = |x: f32, y: f32| (((x - pic.0) / pic.2 * w as f32) as usize, ((y - pic.1) / pic.3 * h as f32) as usize);
        // the word's middle row: coral somewhere along it
        let wy = (l.word.1 + l.word.3) as f32 / 2.0 / l.ky;
        let hits = ((l.word.0 as f32 / l.kx) as i32..(l.word.2 as f32 / l.kx) as i32).filter(|&x| {
            let (tx, ty) = to_tex(x as f32 + 0.5, wy);
            px(tx, ty).x > 0.5
        });
        assert!(hits.count() > 10);
        // the name: grey strokes along its row, nothing else in that row
        let row_y = l.tag.0 as f32 * 2.0 + 1.0;
        let grey = |x: f32| {
            let (tx, ty) = to_tex(x, row_y);
            px(tx, ty).x > 0.15
        };
        let inside = (0..TAG.chars().count() * 8).filter(|&i| grey(l.tag.1 as f32 + i as f32 / 8.0)).count();
        assert!(inside > 10, "the name is drawn ({inside})");
        assert!(!grey(l.tag.1 as f32 - 3.5) && !grey((l.tag.1 + TAG.chars().count()) as f32 + 3.5), "no chip beside it");
    }

    #[test]
    fn a_very_narrow_terminal_keeps_the_underline_on_screen() {
        // portrait: the film is cropped hard; the composition stops growing where the underline
        // would leave the terminal
        for (c, r) in [(60, 60), (40, 50)] {
            let l = layout_for(c, r, 1.0, 1.6);
            let (ua, ub) = l.underline_span();
            let (a, b) = (l.x0 + ua as f32 / l.kx, l.x0 + (ub + 1) as f32 / l.kx);
            assert!(a >= 1.5 && b <= c as f32 - 1.5, "{c}x{r}: underline {a}..{b}");
        }
    }

    #[test]
    fn the_tag_line_does_not_ghost_in_the_crossfade() {
        // the film's tag row is gone by the middle of the crossfade, the terminal's name only
        // comes in after it: never both at once
        let (c, r) = (191, 47);
        let l = layout(c, r, REFERENCE_PA);
        let film = |img: &mut Image| img.px.iter_mut().for_each(|p| *p = V3::splat(0.5));
        for (w, film_left, name_on) in [(0.25, true, false), (0.5, false, false), (0.75, false, true)] {
            let mut img = Image::new(c, r * 2);
            film(&mut img);
            draw(&mut img, 2, REFERENCE_PA, None, w);
            let tag_px = img.get(l.tag.1 + 10, 2 * l.tag.0);
            assert_eq!(tag_px.x > 0.01, film_left, "w={w}: film's tag row {tag_px:?}");
            let name = img.text.iter().find(|(_, _, g)| !g.bold).unwrap().2.fg;
            assert_eq!(name.x > 0.01, name_on, "w={w}: name {name:?}");
        }
    }

    #[test]
    fn the_film_crossfades_into_the_splash_in_register() {
        // where the word's box sits as a share of the film picture: the film shows the reference
        // terminal's layout; every terminal must put it at the same place, also one narrower than
        // the film (its picture cropped at the sides: the splash used to jump in size there)
        let aspect = 1.6;
        let place = |c: usize, r: usize, pa: f32| {
            let l = layout(c, r, pa);
            let pic = crate::intro::grade::picture_rect(c, r * 2, pa, aspect);
            let fx = |x: i32| (l.x0 + x as f32 / l.kx - pic.0) / pic.2;
            let fy = |y: i32| (y as f32 / l.ky - pic.1) / pic.3;
            [fx(l.word.0), fy(l.word.1), fx(l.word.2), fy(l.word.3)]
        };
        let want = place(REFERENCE.0, REFERENCE.1, REFERENCE_PA);
        for (c, r, pa) in [(191, 47, REFERENCE_PA), (160, 40, 1.0), (240, 60, 2.0 / 2.2), (120, 30, 2.0 / 2.1),
                           (120, 40, 2.0 / 2.1), (100, 50, 1.0), (80, 24, 2.0 / 2.2)] {
            let got = place(c, r, pa);
            for k in 0..4 {
                // within a terminal pixel of the reference's picture
                assert!((got[k] - want[k]).abs() < 1.5 / r as f32, "{c}x{r}: {got:?} vs {want:?}");
            }
        }
    }
}
