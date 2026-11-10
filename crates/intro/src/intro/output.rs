//! Terminal encoding: turns a rendered image into escape sequences. Truecolor and 256-colour use
//! Unicode upper half blocks (`▀`, top pixel = foreground, bottom = background) for two pixels per
//! cell; the ASCII fallback uses one character per cell from a luminance ramp. Only cells that
//! changed since the previous frame are written.

use std::io::Write;

use super::grade::{Glyph, Image};
use super::math::{bayer4, V3};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ColorMode {
    TrueColor,
    Ansi256,
    Ascii,
}

impl ColorMode {
    /// Picks a mode from the environment: COLORTERM=truecolor|24bit (or a `direct` TERM) gives
    /// truecolor, a 256-colour TERM gives 256 colours, and anything else, NO_COLOR, or a non-UTF-8
    /// locale gives the ASCII ramp.
    pub fn detect(get: impl Fn(&str) -> Option<String>) -> ColorMode {
        let nonempty = |k: &str| get(k).filter(|v| !v.is_empty());
        if nonempty("NO_COLOR").is_some() {
            return ColorMode::Ascii;
        }
        let locale = nonempty("LC_ALL")
            .or_else(|| nonempty("LC_CTYPE"))
            .or_else(|| nonempty("LANG"))
            .unwrap_or_default();
        let utf8 = {
            let l = locale.to_ascii_lowercase();
            l.contains("utf-8") || l.contains("utf8")
        };
        let term = get("TERM").unwrap_or_default();
        if !utf8 || term.is_empty() || term == "dumb" {
            return ColorMode::Ascii;
        }
        let ct = get("COLORTERM").unwrap_or_default().to_ascii_lowercase();
        if ct == "truecolor" || ct == "24bit" || term.contains("direct") {
            return ColorMode::TrueColor;
        }
        if term.contains("256") {
            return ColorMode::Ansi256;
        }
        ColorMode::Ascii
    }

    pub fn parse(s: &str) -> Option<ColorMode> {
        match s {
            "truecolor" | "24bit" => Some(ColorMode::TrueColor),
            "256" => Some(ColorMode::Ansi256),
            "ascii" => Some(ColorMode::Ascii),
            _ => None,
        }
    }

    /// Image pixels per cell vertically.
    pub fn pixels_per_cell(self) -> usize {
        match self {
            ColorMode::Ascii => 1,
            _ => 2,
        }
    }
}

/// The ASCII luminance ramp, darkest first.
pub const RAMP: &[u8] = b" .:-=+*#%@";

/// Levels for the ASCII ramp: the near-black background maps to blanks, the lit subject spreads
/// over the whole ramp.
pub fn ascii_level(luma: f32) -> f32 {
    const BLACK: f32 = 0.075;
    const WHITE: f32 = 0.55;
    ((luma - BLACK) / (WHITE - BLACK))
        .clamp(0.0, 1.0)
        .powf(0.85)
}

/// A terminal colour: 24-bit, palette index, or the terminal's default.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Color {
    Rgb(u8, u8, u8),
    Idx(u8),
    Default,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Cell {
    ch: char,
    fg: Color,
    bg: Color,
    bold: bool,
}

/// Per-channel difference below which a truecolor cell is left as it is on screen (and within
/// which the pen's colour is reused for a neighbour). Two levels out of 255 are invisible but
/// skipping them saves a large share of the output bytes. A terminal or link that can't keep up
/// gets a coarser one (`Encoder::set_tolerance`) before it gets fewer frames.
pub const TOLERANCE: u8 = 2;

#[cfg(test)]
fn color_close(a: Color, b: Color) -> bool {
    close(a, b, TOLERANCE)
}

/// Same within `tol` levels per channel (truecolor); other colours only when equal.
fn close(a: Color, b: Color, tol: u8) -> bool {
    match (a, b) {
        (Color::Rgb(r1, g1, b1), Color::Rgb(r2, g2, b2)) => {
            r1.abs_diff(r2) <= tol && g1.abs_diff(g2) <= tol && b1.abs_diff(b2) <= tol
        }
        _ => a == b,
    }
}

impl Cell {
    /// Same on screen within the tolerance: half-block pictures with close colours in both halves
    /// (however they are drawn), or the same character in close colours.
    fn looks_like(&self, o: &Cell, tol: u8) -> bool {
        let color_close = |a, b| close(a, b, tol);
        match (self.halves(), o.halves()) {
            (Some((t1, b1)), Some((t2, b2))) => color_close(t1, t2) && color_close(b1, b2),
            _ => {
                self.ch == o.ch
                    && self.bold == o.bold
                    && color_close(self.bg, o.bg)
                    && (self.ch == ' ' || color_close(self.fg, o.fg))
            }
        }
    }

    /// Top and bottom colour of a picture cell (a half block or a blank).
    fn halves(&self) -> Option<(Color, Color)> {
        match self.ch {
            ' ' => Some((self.bg, self.bg)),
            HALF => Some((self.fg, self.bg)),
            LOWER => Some((self.bg, self.fg)),
            _ => None,
        }
    }

    /// The cheapest way to draw this picture cell with the pen `(fg, bg)` already set: upper or
    /// lower half block (or a blank), and the pen's own colours wherever they are within the
    /// tolerance of the wanted ones, so neighbouring cells in a smooth picture rarely need a
    /// colour change. Text cells are drawn as they are.
    fn cheapest(self, pen: Option<(Color, Color)>, tol: u8) -> Cell {
        let color_close = |a, b| close(a, b, tol);
        let (Some((top, bot)), Some((pf, pb))) = (self.halves(), pen) else {
            return self;
        };
        let near = |c: Color, pen: Color| if color_close(c, pen) { pen } else { c };
        let mut options = Vec::with_capacity(3);
        if color_close(top, bot) {
            // a blank must stay within the tolerance of both halves
            let bg = if color_close(pb, top) && color_close(pb, bot) { pb } else { top };
            options.push(Cell { ch: ' ', fg: Color::Default, bg, bold: false });
        }
        options.push(Cell { ch: HALF, fg: near(top, pf), bg: near(bot, pb), bold: false });
        options.push(Cell { ch: LOWER, fg: near(bot, pf), bg: near(top, pb), bold: false });
        let changes = |c: &Cell| (c.ch != ' ' && c.fg != pf) as u8 + (c.bg != pb) as u8;
        // fewest colour changes; ties go to a blank (one byte, and the only right way to draw the
        // default colours), then the plain upper half block
        options.into_iter().min_by_key(changes).unwrap_or(self)
    }
}

const HALF: char = '▀';
const LOWER: char = '▄';

fn to_u8(v: f32) -> u8 {
    (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8
}

const LEVELS: [f32; 6] = [0.0, 95.0, 135.0, 175.0, 215.0, 255.0];

fn dist_256(a: [f32; 3], b: [f32; 3]) -> f32 {
    let (dr, dg, db) = (a[0] - b[0], a[1] - b[1], a[2] - b[2]);
    0.3 * dr * dr + 0.59 * dg * dg + 0.11 * db * db
}

/// Nearest entry of the 6x6x6 cube (16..231) to an RGB value in 0..255.
fn nearest_cube(rgb: [f32; 3]) -> (u8, [f32; 3]) {
    let level = |v: f32| {
        let mut best = 0;
        for (i, l) in LEVELS.iter().enumerate() {
            if (v - l).abs() < (v - LEVELS[best]).abs() {
                best = i;
            }
        }
        best
    };
    let (r, g, b) = (level(rgb[0]), level(rgb[1]), level(rgb[2]));
    (
        16 + 36 * r as u8 + 6 * g as u8 + b as u8,
        [LEVELS[r], LEVELS[g], LEVELS[b]],
    )
}

/// Nearest entry of the grey ramp (232..255).
fn nearest_grey(rgb: [f32; 3]) -> (u8, [f32; 3]) {
    let lum = 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2];
    let k = ((lum - 8.0) / 10.0).round().clamp(0.0, 23.0);
    let v = 8.0 + 10.0 * k;
    (232 + k as u8, [v, v, v])
}

/// xterm-256 colour for `c` (display 0..1) with ordered dithering: `d` in -0.5..0.5 is the dither
/// threshold. The colour is approximated by mixing its nearest palette entry with a second entry of
/// the same family (cube or grey ramp) on the other side of it; saturated colours dither only their
/// dominant channel, so a dark burgundy dithers between dark reds and black, never red and grey.
pub fn to_256(c: V3, d: f32) -> u8 {
    let rgb = [
        c.x.clamp(0.0, 1.0) * 255.0,
        c.y.clamp(0.0, 1.0) * 255.0,
        c.z.clamp(0.0, 1.0) * 255.0,
    ];
    let (cube, grey) = (nearest_cube(rgb), nearest_grey(rgb));
    // strongly saturated colours stay in the cube even when a grey is nearer by luma: hue matters
    // more than a level of brightness (a dark burgundy must not turn grey)
    let (hi, lo) = (
        rgb[0].max(rgb[1]).max(rgb[2]),
        rgb[0].min(rgb[1]).min(rgb[2]),
    );
    let saturated = hi > 24.0 && (hi - lo) / hi > 0.6;
    let use_cube = saturated || dist_256(cube.1, rgb) <= dist_256(grey.1, rgb);
    let (ia, a) = if use_cube { cube } else { grey };
    if saturated {
        // dither only the dominant channel between its two neighbouring cube levels; the minor
        // channels stay put, so a red never sparkles with olive or pink pixels
        let k = (0..3)
            .max_by(|&i, &j| rgb[i].total_cmp(&rgb[j]))
            .unwrap_or(0);
        let li = LEVELS.iter().position(|&l| l == a[k]).unwrap_or(0);
        let lj = if rgb[k] >= a[k] {
            (li + 1).min(5)
        } else {
            li.saturating_sub(1)
        };
        if li == lj {
            return ia;
        }
        let t = (rgb[k] - a[k]) / (LEVELS[lj] - LEVELS[li]);
        let step = [36u8, 6, 1][k];
        let ib = if lj > li { ia + step } else { ia - step };
        return if d + 0.5 < t.clamp(0.0, 1.0) { ib } else { ia };
    }
    let over = [
        (2.0 * rgb[0] - a[0]).clamp(0.0, 255.0),
        (2.0 * rgb[1] - a[1]).clamp(0.0, 255.0),
        (2.0 * rgb[2] - a[2]).clamp(0.0, 255.0),
    ];
    let (ib, b) = if use_cube {
        nearest_cube(over)
    } else {
        nearest_grey(over)
    };
    if ib == ia {
        return ia;
    }
    let ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    let len2 = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2];
    let t = ((rgb[0] - a[0]) * ab[0] + (rgb[1] - a[1]) * ab[1] + (rgb[2] - a[2]) * ab[2]) / len2;
    if d + 0.5 < t.clamp(0.0, 1.0) {
        ib
    } else {
        ia
    }
}

/// Converts an image to cells for `mode`. The image must be `cols` wide and
/// `rows * mode.pixels_per_cell()` tall.
fn cells(img: &Image, mode: ColorMode, cols: usize, rows: usize, default_bg: bool) -> Vec<Cell> {
    let mut out = Vec::with_capacity(cols * rows);
    let px = |x: usize, y: usize| img.px[y * img.w + x];
    let mut text: Vec<Option<Glyph>> = vec![None; cols * rows];
    for &(c, r, g) in &img.text {
        if c < cols && r < rows {
            text[r * cols + c] = Some(g);
        }
    }
    for row in 0..rows {
        for x in 0..cols {
            let cell = match mode {
                ColorMode::TrueColor | ColorMode::Ansi256 => {
                    let (top, bot) = (px(x, row * 2), px(x, row * 2 + 1));
                    let conv = |c: V3, y: usize| match mode {
                        ColorMode::TrueColor => Color::Rgb(to_u8(c.x), to_u8(c.y), to_u8(c.z)),
                        _ => Color::Idx(to_256(c, bayer4(x, y) - 0.5)),
                    };
                    if let Some(g) = text[row * cols + x] {
                        // a character on its own background, or the average of the cell's pixels
                        Cell {
                            ch: g.ch,
                            fg: if g.ch == ' ' { Color::Default } else { conv(g.fg, row * 2) },
                            bg: conv(g.bg.unwrap_or((top + bot) * 0.5), row * 2 + 1),
                            bold: g.bold,
                        }
                    } else {
                        let (fg, bg) = (conv(top, row * 2), conv(bot, row * 2 + 1));
                        if fg == bg {
                            Cell {
                                ch: ' ',
                                fg: Color::Default,
                                bg,
                                bold: false,
                            }
                        } else {
                            Cell { ch: HALF, fg, bg, bold: false }
                        }
                    }
                }
                ColorMode::Ascii if text[row * cols + x].is_some() => {
                    let g = text[row * cols + x].unwrap();
                    Cell {
                        ch: super::splash::ascii(g.ch) as char,
                        fg: Color::Default,
                        bg: Color::Default,
                        bold: g.bold,
                    }
                }
                ColorMode::Ascii => {
                    let l = ascii_level(px(x, row).luma())
                        + (bayer4(x, row) - 0.5) * 0.6 / RAMP.len() as f32;
                    let i = ((l.clamp(0.0, 0.999)) * RAMP.len() as f32) as usize;
                    Cell {
                        ch: RAMP[i.min(RAMP.len() - 1)] as char,
                        fg: Color::Default,
                        bg: Color::Default,
                        bold: false,
                    }
                }
            };
            out.push(if default_bg {
                Cell {
                    ch: ' ',
                    fg: Color::Default,
                    bg: Color::Default,
                    bold: false,
                }
            } else {
                cell
            });
        }
    }
    out
}

fn push_color(out: &mut Vec<u8>, c: Color, fg: bool) {
    match c {
        Color::Rgb(r, g, b) => {
            let _ = write!(out, "{};2;{};{};{}", if fg { 38 } else { 48 }, r, g, b);
        }
        Color::Idx(i) => {
            let _ = write!(out, "{};5;{}", if fg { 38 } else { 48 }, i);
        }
        Color::Default => {
            let _ = write!(out, "{}", if fg { 39 } else { 49 });
        }
    }
}

/// Diffing encoder: remembers what is on screen and writes only what changed.
#[derive(Clone)]
pub struct Encoder {
    pub mode: ColorMode,
    pub cols: usize,
    pub rows: usize,
    prev: Option<Vec<Cell>>,
    /// Write every change exactly (no tolerance): for the native splash, where nothing may be
    /// left over from the film.
    exact: bool,
    tolerance: u8,
}

impl Encoder {
    pub fn new(mode: ColorMode, cols: usize, rows: usize) -> Encoder {
        Encoder {
            mode,
            cols,
            rows,
            prev: None,
            exact: false,
            tolerance: TOLERANCE,
        }
    }

    /// The tolerance used outside exact mode (levels per channel; at least [`TOLERANCE`]).
    pub fn set_tolerance(&mut self, t: u8) {
        self.tolerance = t.max(TOLERANCE);
    }

    /// Switch exact mode; switching on redraws the whole screen exactly on the next frame, so
    /// nothing within the tolerance of the film stays behind.
    pub fn set_exact(&mut self, on: bool) {
        if on != self.exact {
            self.exact = on;
            if on {
                self.prev = None;
            }
        }
    }

    /// Forget the screen contents (after a resize or clear): the next frame is drawn in full.
    pub fn invalidate(&mut self) {
        self.prev = None;
    }

    /// Appends the escape sequences that turn the previous frame into `img` to `out`.
    /// `default_bg` paints every cell blank in the terminal's default colours (the final hand-off).
    pub fn encode(&mut self, img: &Image, default_bg: bool, out: &mut Vec<u8>) {
        let mut cur = cells(img, self.mode, self.cols, self.rows, default_bg);
        let tol = if self.exact { 0 } else { self.tolerance };
        let mut cursor: Option<(usize, usize)> = None;
        let mut pen: Option<(Color, Color)> = None;
        // every frame that writes anything ends with a reset: bold starts off
        let mut bold = false;
        for row in 0..self.rows {
            for col in 0..self.cols {
                let i = row * self.cols + col;
                if let Some(prev) = &self.prev {
                    if prev[i].looks_like(&cur[i], tol) {
                        // keep remembering what is actually on screen, so slow drifts still update
                        cur[i] = prev[i];
                        continue;
                    }
                }
                // remember the cell as drawn (it may use the pen's colours)
                let c = cur[i].cheapest(pen, tol);
                cur[i] = c;
                match cursor {
                    Some((r, c0)) if r == row && c0 == col => {}
                    Some((r, c0)) if r == row && c0 < col => {
                        let _ = write!(out, "\x1b[{}C", col - c0);
                    }
                    _ => {
                        let _ = write!(out, "\x1b[{};{}H", row + 1, col + 1);
                    }
                }
                let want_fg = if c.ch == ' ' {
                    pen.map(|p| p.0).unwrap_or(Color::Default)
                } else {
                    c.fg
                };
                let want = (want_fg, c.bg);
                // a blank shows no glyph: it keeps whatever weight is set
                let want_bold = if c.ch == ' ' { bold } else { c.bold };
                if pen != Some(want) || want_bold != bold {
                    out.extend_from_slice(b"\x1b[");
                    let (pf, pb) = match pen {
                        Some(p) => (Some(p.0), Some(p.1)),
                        None => (None, None),
                    };
                    let mut first = true;
                    if want_bold != bold {
                        out.extend_from_slice(if want_bold { b"1" } else { b"22" });
                        bold = want_bold;
                        first = false;
                    }
                    if pf != Some(want.0) {
                        if !first {
                            out.push(b';');
                        }
                        push_color(out, want.0, true);
                        first = false;
                    }
                    if pb != Some(want.1) {
                        if !first {
                            out.push(b';');
                        }
                        push_color(out, want.1, false);
                    }
                    out.push(b'm');
                    pen = Some(want);
                }
                let mut utf8 = [0u8; 4];
                out.extend_from_slice(c.ch.encode_utf8(&mut utf8).as_bytes());
                // autowrap is off while we draw: the cursor sticks at the last column
                cursor = if col + 1 < self.cols {
                    Some((row, col + 1))
                } else {
                    None
                };
            }
        }
        if pen.is_some() {
            out.extend_from_slice(b"\x1b[0m");
        }
        self.prev = Some(cur);
    }

    /// The whole image as plain lines (no cursor addressing), for `--ansi` stills.
    pub fn encode_lines(&self, img: &Image, out: &mut Vec<u8>) {
        let cur = cells(img, self.mode, self.cols, self.rows, false);
        for row in 0..self.rows {
            let mut pen: Option<(Color, Color, bool)> = None;
            for col in 0..self.cols {
                let c = cur[row * self.cols + col];
                let want = (if c.ch == ' ' { Color::Default } else { c.fg }, c.bg, c.bold);
                if pen != Some(want) {
                    out.extend_from_slice(if want.2 { b"\x1b[0;1;" } else { b"\x1b[0;" });
                    push_color(out, want.0, true);
                    out.push(b';');
                    push_color(out, want.1, false);
                    out.push(b'm');
                    pen = Some(want);
                }
                let mut utf8 = [0u8; 4];
                out.extend_from_slice(c.ch.encode_utf8(&mut utf8).as_bytes());
            }
            out.extend_from_slice(b"\x1b[0m\n");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::intro::math::v3;

    fn env<'a>(pairs: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<String> + 'a {
        move |k| {
            pairs
                .iter()
                .find(|(n, _)| *n == k)
                .map(|(_, v)| v.to_string())
        }
    }

    #[test]
    fn detects_colour_support() {
        let utf = ("LANG", "en_US.UTF-8");
        assert_eq!(
            ColorMode::detect(env(&[
                utf,
                ("TERM", "xterm-256color"),
                ("COLORTERM", "truecolor")
            ])),
            ColorMode::TrueColor
        );
        assert_eq!(
            ColorMode::detect(env(&[utf, ("TERM", "xterm-256color")])),
            ColorMode::Ansi256
        );
        assert_eq!(
            ColorMode::detect(env(&[utf, ("TERM", "xterm")])),
            ColorMode::Ascii
        );
        assert_eq!(
            ColorMode::detect(env(&[utf, ("TERM", "xterm-direct")])),
            ColorMode::TrueColor
        );
        assert_eq!(
            ColorMode::detect(env(&[("LANG", "C"), ("TERM", "xterm-256color")])),
            ColorMode::Ascii
        );
        assert_eq!(
            ColorMode::detect(env(&[
                utf,
                ("TERM", "xterm-256color"),
                ("COLORTERM", "truecolor"),
                ("NO_COLOR", "1")
            ])),
            ColorMode::Ascii
        );
        assert_eq!(
            ColorMode::detect(env(&[utf, ("TERM", "dumb")])),
            ColorMode::Ascii
        );
        assert_eq!(ColorMode::detect(env(&[utf])), ColorMode::Ascii);
    }

    #[test]
    fn quantises_to_256() {
        assert_eq!(to_256(v3(0.0, 0.0, 0.0), 0.0), 16);
        assert_eq!(to_256(v3(1.0, 1.0, 1.0), 0.0), 231);
        assert_eq!(to_256(v3(95.0 / 255.0, 0.0, 0.0), 0.0), 52); // burgundy-ish cube colour
        let g = to_256(v3(0.5, 0.5, 0.5), 0.0);
        assert!(
            (232..=255).contains(&g),
            "mid grey uses the grey ramp, got {g}"
        );
        // a dark burgundy dithers between pure reds and black only: 16 (black) or 52 (95,0,0)
        let burgundy = v3(60.0 / 255.0, 14.0 / 255.0, 12.0 / 255.0);
        let picks: Vec<u8> = (0..16)
            .map(|k| to_256(burgundy, (k as f32 + 0.5) / 16.0 - 0.5))
            .collect();
        assert!(picks.iter().all(|&i| i == 16 || i == 52), "{picks:?}");
        assert!(
            picks.contains(&16) && picks.contains(&52),
            "it does dither: {picks:?}"
        );
    }

    fn image(w: usize, h: usize, f: impl Fn(usize, usize) -> V3) -> Image {
        let mut img = Image::new(w, h);
        for y in 0..h {
            for x in 0..w {
                img.px[y * w + x] = f(x, y);
            }
        }
        img
    }

    #[test]
    fn unchanged_frame_writes_nothing() {
        let img = image(4, 4, |x, y| v3(x as f32 / 4.0, y as f32 / 4.0, 0.2));
        let mut enc = Encoder::new(ColorMode::TrueColor, 4, 2);
        let mut out = Vec::new();
        enc.encode(&img, false, &mut out);
        assert!(!out.is_empty());
        let mut again = Vec::new();
        enc.encode(&img, false, &mut again);
        assert!(again.is_empty(), "no change, no output");
        enc.invalidate();
        let mut full = Vec::new();
        enc.encode(&img, false, &mut full);
        assert_eq!(full, out);
    }

    #[test]
    fn tiny_changes_are_skipped_but_accumulate() {
        let a = image(2, 2, |_, _| v3(0.5, 0.5, 0.5));
        let mut enc = Encoder::new(ColorMode::TrueColor, 2, 1);
        let mut out = Vec::new();
        enc.encode(&a, false, &mut out);
        let step = 1.0 / 255.0;
        let mut written = 0;
        for k in 1..=6 {
            let img = image(2, 2, |_, _| V3::splat(0.5 + step * k as f32));
            out.clear();
            enc.encode(&img, false, &mut out);
            if !out.is_empty() {
                written += 1;
            }
        }
        assert!(
            (1..=2).contains(&written),
            "a slow drift updates once it exceeds the tolerance ({written})"
        );
    }

    #[test]
    fn one_changed_pixel_writes_one_cell() {
        let a = image(8, 8, |_, _| v3(0.1, 0.1, 0.1));
        let mut b = a.clone();
        b.px[5 * 8 + 3] = v3(0.9, 0.2, 0.1); // row 5 -> cell row 2, col 3
        let mut enc = Encoder::new(ColorMode::TrueColor, 8, 4);
        let mut out = Vec::new();
        enc.encode(&a, false, &mut out);
        out.clear();
        enc.encode(&b, false, &mut out);
        let s = String::from_utf8(out).unwrap();
        assert!(s.starts_with("\x1b[3;4H"), "{s:?}");
        assert_eq!(s.matches('▀').count(), 1);
    }

    #[test]
    fn half_block_colours_and_solid_cells() {
        let img = image(2, 2, |x, y| {
            if x == 0 && y == 0 {
                v3(1.0, 0.0, 0.0)
            } else {
                v3(0.0, 0.0, 1.0)
            }
        });
        let mut enc = Encoder::new(ColorMode::TrueColor, 2, 1);
        let mut out = Vec::new();
        enc.encode(&img, false, &mut out);
        let s = String::from_utf8(out).unwrap();
        assert!(
            s.contains("38;2;255;0;0") && s.contains("48;2;0;0;255"),
            "{s:?}"
        );
        assert!(
            s.contains("▀ "),
            "second cell is solid blue: a space on blue background: {s:?}"
        );
    }

    #[test]
    fn ascii_ramp_by_luminance() {
        let img = image(3, 1, |x, _| V3::splat([0.02, 0.3, 1.0][x]));
        let enc = Encoder::new(ColorMode::Ascii, 3, 1);
        let mut out = Vec::new();
        enc.encode_lines(&img, &mut out);
        let s = String::from_utf8(out).unwrap();
        let mut text = String::new();
        let mut in_esc = false;
        for ch in s.chars() {
            match (in_esc, ch) {
                (false, '\x1b') => in_esc = true,
                (true, 'm') => in_esc = false,
                (false, '\n') => {}
                (false, c) => text.push(c),
                _ => {}
            }
        }
        assert_eq!(text.chars().count(), 3, "{text:?}");
        assert!(text.ends_with('@'), "white is '@': {s:?}");
        assert!(text.contains(' '), "the dark background is blank: {s:?}");
    }

    #[test]
    fn default_bg_blanks_everything() {
        let img = image(2, 2, |_, _| v3(0.5, 0.5, 0.5));
        let mut enc = Encoder::new(ColorMode::TrueColor, 2, 1);
        let mut out = Vec::new();
        enc.encode(&img, true, &mut out);
        let s = String::from_utf8(out).unwrap();
        assert!(s.contains("49") && !s.contains('▀'), "{s:?}");
    }

    /// A minimal truecolor terminal for the encoder's own output: cursor moves (CUP, CUF), SGR
    /// colours, and the three picture characters. Returns each cell's (top, bottom) colour.
    fn emulate(out: &[u8], cols: usize, screen: &mut [(Color, Color)]) {
        let s = std::str::from_utf8(out).unwrap();
        let (mut cur, mut fg, mut bg) = (0usize, Color::Default, Color::Default);
        let mut chars = s.chars().peekable();
        while let Some(ch) = chars.next() {
            if ch == '\x1b' {
                assert_eq!(chars.next(), Some('['));
                let mut params = String::new();
                let fin = loop {
                    let c = chars.next().unwrap();
                    if c.is_ascii_digit() || c == ';' || c == '?' {
                        params.push(c);
                    } else {
                        break c;
                    }
                };
                let nums: Vec<usize> = params.split(';').filter_map(|p| p.parse().ok()).collect();
                match fin {
                    'H' => cur = (nums[0] - 1) * cols + nums[1] - 1,
                    'C' => cur += nums[0],
                    'm' => {
                        let mut k = 0;
                        while k < nums.len() {
                            match nums[k] {
                                0 => (fg, bg) = (Color::Default, Color::Default),
                                1 | 22 => {}
                                38 | 48 => {
                                    let c = Color::Rgb(nums[k + 2] as u8, nums[k + 3] as u8, nums[k + 4] as u8);
                                    if nums[k] == 38 { fg = c } else { bg = c }
                                    k += 4;
                                }
                                39 => fg = Color::Default,
                                49 => bg = Color::Default,
                                n => panic!("unexpected SGR {n}"),
                            }
                            k += 1;
                        }
                    }
                    f => panic!("unexpected sequence {f}"),
                }
                continue;
            }
            screen[cur] = match ch {
                ' ' => (bg, bg),
                HALF => (fg, bg),
                LOWER => (bg, fg),
                c => panic!("unexpected char {c:?}"),
            };
            cur += 1;
        }
    }

    #[test]
    fn screen_stays_within_the_tolerance_and_reuses_the_pen() {
        let (cols, rows) = (40, 12);
        let mut enc = Encoder::new(ColorMode::TrueColor, cols, rows);
        let mut screen = vec![(Color::Default, Color::Default); cols * rows];
        let (mut bytes, mut sgr, mut cells_written) = (0, 0, 0);
        for f in 0..30 {
            // a smooth gradient that drifts, with a sharp moving bar and a little noise
            let img = image(cols, rows * 2, |x, y| {
                let g = (x as f32 * 0.01 + y as f32 * 0.004 + f as f32 * 0.003).fract();
                let bar = (x + f) % 17 < 3;
                let n = crate::intro::math::hash01(x as u32, y as u32, f as u32) * 0.004;
                if bar { v3(0.9, 0.3, 0.2) } else { v3(0.2 + g * 0.5 + n, 0.25 + g * 0.3, 0.3) }
            });
            let mut out = Vec::new();
            enc.encode(&img, false, &mut out);
            bytes += out.len();
            sgr += out.windows(2).filter(|w| w == b"m\xe2" || w == b"m ").count();
            cells_written += String::from_utf8_lossy(&out).chars().filter(|c| matches!(*c, ' ' | HALF | LOWER)).count();
            emulate(&out, cols, &mut screen);
            for row in 0..rows {
                for x in 0..cols {
                    let want = |y: usize| {
                        let c = img.px[y * cols + x];
                        Color::Rgb(to_u8(c.x), to_u8(c.y), to_u8(c.z))
                    };
                    let (t, b) = screen[row * cols + x];
                    assert!(
                        color_close(t, want(row * 2)) && color_close(b, want(row * 2 + 1)),
                        "frame {f} cell {x},{row}: {:?} vs {:?}",
                        (t, b),
                        (want(row * 2), want(row * 2 + 1))
                    );
                }
            }
        }
        assert!(bytes > 0 && cells_written > 0 && sgr > 0);
        // a gentle gradient (a fraction of a level per cell) needs few colour changes
        let img = image(cols, rows * 2, |x, y| v3(0.3 + x as f32 * 0.0008, 0.3, 0.3 + y as f32 * 0.0005));
        let mut out = Vec::new();
        Encoder::new(ColorMode::TrueColor, cols, rows).encode(&img, false, &mut out);
        let changes = out.windows(2).filter(|w| w == b"m\xe2" || w == b"m ").count();
        assert!(changes * 8 < cols * rows, "pen reused: {changes} colour changes for {} cells", cols * rows);
        let mut flat = vec![(Color::Default, Color::Default); cols * rows];
        emulate(&out, cols, &mut flat);
    }

    #[test]
    fn exact_mode_leaves_nothing_of_the_film_behind() {
        let (cols, rows) = (30, 10);
        // the last crossfade frame: near-black film noise, within the tolerance of black
        let noisy = image(cols, rows * 2, |x, y| V3::splat(((x * 7 + y * 3) % 3) as f32 / 255.0));
        let black = image(cols, rows * 2, |_, _| V3::ZERO);
        let leftovers = |exact: bool| {
            let mut enc = Encoder::new(ColorMode::TrueColor, cols, rows);
            let mut screen = vec![(Color::Default, Color::Default); cols * rows];
            // (the noise put on screen exactly, as film grain leaves it)
            for (img, ex) in [(&noisy, true), (&black, exact)] {
                enc.set_exact(ex);
                let mut out = Vec::new();
                enc.encode(img, false, &mut out);
                emulate(&out, cols, &mut screen);
            }
            screen.iter().filter(|&&c| c != (Color::Rgb(0, 0, 0), Color::Rgb(0, 0, 0))).count()
        };
        assert!(leftovers(false) > 0, "the tolerance keeps the film's noise on screen");
        assert_eq!(leftovers(true), 0, "exact: the splash's flat black everywhere");
    }

    #[test]
    fn a_coarser_tolerance_writes_fewer_cells() {
        let (cols, rows) = (40, 12);
        let a = image(cols, rows * 2, |x, y| v3(0.3 + x as f32 * 0.002, 0.3, 0.3 + y as f32 * 0.001));
        let b = image(cols, rows * 2, |x, y| v3(0.3 + x as f32 * 0.002 + 0.016, 0.3, 0.3 + y as f32 * 0.001));
        let bytes = |tol: u8| {
            // the first frame at the normal tolerance, the change at `tol`
            let mut enc = Encoder::new(ColorMode::TrueColor, cols, rows);
            let mut out = Vec::new();
            enc.encode(&a, false, &mut out);
            out.clear();
            enc.set_tolerance(tol);
            enc.encode(&b, false, &mut out); // every pixel 4 levels brighter
            out.len()
        };
        assert!(bytes(2) > 0, "a change of 4 levels is written at the normal tolerance");
        assert_eq!(bytes(6), 0, "... and left alone at a coarser one");
        let mut enc = Encoder::new(ColorMode::TrueColor, cols, rows);
        enc.set_tolerance(0);
        assert_eq!(enc.tolerance, TOLERANCE, "never finer than the normal tolerance (exact mode is for that)");
    }
}
