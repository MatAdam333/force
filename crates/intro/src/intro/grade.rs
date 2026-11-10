//! Puts a film frame on the terminal's pixel grid: scales it to cover the whole terminal (the
//! film is rendered taller than its 2.39:1 composition, so a 16:9 terminal crops only a little off
//! its top and bottom; a narrow one crops its sides), then adds what depends on playback rather than on the film: the fade
//! up from black, a soft vignette and film grain. [`finish`] fades the whole picture (film and
//! splash) into the app's screen.

use std::sync::Mutex;

use super::math::{hash01, smoothstep, v3, V3};
use super::timing as T;

/// A character drawn in a cell on top of the pixels (the splash's box and name).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Glyph {
    pub ch: char,
    /// Display RGB; the cell's background is the average of its pixels.
    pub fg: V3,
    pub bold: bool,
    /// The cell's own background; `None` = the average of the cell's pixels.
    pub bg: Option<V3>,
}

/// Display-referred image (0..1 per channel), row-major, plus characters on top of it.
#[derive(Clone, Debug)]
pub struct Image {
    pub w: usize,
    pub h: usize,
    pub px: Vec<V3>,
    /// (column, row, glyph) in terminal cells.
    pub text: Vec<(usize, usize, Glyph)>,
}

impl Image {
    pub fn new(w: usize, h: usize) -> Image {
        Image {
            w,
            h,
            px: vec![V3::ZERO; w * h],
            text: Vec::new(),
        }
    }
    pub fn get(&self, x: usize, y: usize) -> V3 {
        self.px[y * self.w + x]
    }
}

const VIGNETTE: f32 = 0.28;
const GRAIN: f32 = 0.018;

/// Vignette brightness at (`nx`, `ny`), the position in the picture from its centre (-1..1).
pub fn vignette(nx: f32, ny: f32) -> f32 {
    let r = ((nx * nx * 0.75 + ny * ny * 0.6) / 1.35).sqrt();
    1.0 - VIGNETTE * smoothstep(0.35, 1.1, r)
}

/// How to show a frame: resolution-independent settings.
#[derive(Clone, Copy, Debug)]
pub struct Settings {
    /// Width / height of one image pixel on screen (1.0 for half-blocks on a 2:1 cell).
    pub pixel_aspect: f32,
    /// Film grain seed; `None` = static grain (no per-frame change).
    pub grain_seed: Option<u32>,
    /// Colour the end fades into (display RGB 0..1).
    pub fade_to: V3,
}

/// Fades `img` (pixels and characters) toward `to` by `k` and clamps it to 0..1.
pub fn finish(img: &mut Image, to: V3, k: f32) {
    let clamp = |c: V3| v3(c.x.clamp(0.0, 1.0), c.y.clamp(0.0, 1.0), c.z.clamp(0.0, 1.0));
    for p in &mut img.px {
        *p = clamp(p.lerp(to, k));
    }
    for (_, _, g) in &mut img.text {
        g.fg = clamp(g.fg.lerp(to, k));
        g.bg = g.bg.map(|b| clamp(b.lerp(to, k)));
    }
}

/// Where a picture of `aspect` sits in a `w` x `h` image whose pixels are `pixel_aspect` wide per
/// unit height: scaled to cover it, centred, overhanging two of its edges (cropped there).
/// (x0, y0, width, height) in image pixels, fractional; x0 or y0 is negative where it overhangs.
pub fn picture_rect(w: usize, h: usize, pixel_aspect: f32, aspect: f32) -> (f32, f32, f32, f32) {
    let (w, h) = (w as f32, h as f32);
    let pic_h = w * pixel_aspect / aspect;
    if pic_h >= h {
        (0.0, (h - pic_h) * 0.5, w, pic_h)
    } else {
        let pic_w = h * aspect / pixel_aspect;
        ((w - pic_w) * 0.5, 0.0, pic_w, h)
    }
}

/// Resampling weights from `src` samples onto `n` output pixels covering source span
/// `[0, src)` with output pixel 0 starting at `offset` (output pixels): a tent filter as wide as
/// the scale, so shrinking averages (no aliasing) and enlarging interpolates.
fn weights(n: usize, offset: f32, span: f32, src: usize) -> Weights {
    let scale = src as f32 / span; // source pixels per output pixel
    let radius = scale.max(1.0);
    (0..n)
        .map(|i| {
            let centre = (i as f32 + 0.5 - offset) * scale; // in source pixels
            let (lo, hi) = (
                (centre - radius).floor().max(0.0) as usize,
                ((centre + radius).ceil() as usize).min(src),
            );
            let mut w: Vec<(usize, f32)> = (lo..hi)
                .map(|s| {
                    (
                        s,
                        (1.0 - ((s as f32 + 0.5 - centre) / radius).abs()).max(0.0),
                    )
                })
                .filter(|&(_, wt)| wt > 0.0)
                .collect();
            let sum: f32 = w.iter().map(|p| p.1).sum();
            if sum > 0.0 {
                for p in &mut w {
                    p.1 /= sum;
                }
            } else if !(lo..hi).is_empty() {
                w.push((lo.min(src - 1), 1.0));
            }
            w
        })
        .collect()
}

type Weights = Vec<Vec<(usize, f32)>>;
/// Output size, film size and pixel aspect the cached weights were made for.
type CacheKey = (usize, usize, usize, usize, u32);
type Cached = Option<(CacheKey, (Weights, Weights))>;

/// Composes film frame `src` (linear RGB, `sw` x `sh`) at time `t` into a `w` x `h` image
/// (not yet faded out: see [`finish`]).
pub fn compose(
    src: &[V3],
    sw: usize,
    sh: usize,
    t: f32,
    w: usize,
    h: usize,
    st: &Settings,
) -> Image {
    let mut img = Image::new(w, h);
    if w == 0 || h == 0 {
        return img;
    }
    let fade_in = smoothstep(0.0, T::FADE_IN_END, t);
    if fade_in > 0.0 {
        let (x0, y0, pw, ph) = picture_rect(w, h, st.pixel_aspect, sw as f32 / sh as f32);
        // the weights only depend on the sizes: cache them across frames
        static CACHE: Mutex<Cached> = Mutex::new(None);
        let key = (w, h, sw, sh, st.pixel_aspect.to_bits());
        let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
        if cache.as_ref().map(|c| c.0) != Some(key) {
            *cache = Some((key, (weights(w, x0, pw, sw), weights(h, y0, ph, sh))));
        }
        let (wx, wy) = &cache.as_ref().unwrap().1;
        let grain_seed = st.grain_seed.unwrap_or(0x5eed);
        let (cx, cy) = (x0 + pw * 0.5, y0 + ph * 0.5);
        for (y, wrow) in wy.iter().enumerate() {
            let yc = y as f32 + 0.5;
            // fraction of this row inside the picture (soft edge where it cuts a row)
            let cover_y = ((y as f32 + 1.0).min(y0 + ph) - (y as f32).max(y0)).clamp(0.0, 1.0);
            if cover_y <= 0.0 {
                continue;
            }
            for (x, wcol) in wx.iter().enumerate() {
                let cover_x = ((x as f32 + 1.0).min(x0 + pw) - (x as f32).max(x0)).clamp(0.0, 1.0);
                if cover_x <= 0.0 {
                    continue;
                }
                let mut lin = V3::ZERO;
                for &(sy, wyv) in wrow {
                    let row = &src[sy * sw..];
                    for &(sx, wxv) in wcol {
                        lin += row[sx] * (wxv * wyv);
                    }
                }
                let mut d = v3(
                    super::film::to_display(lin.x),
                    super::film::to_display(lin.y),
                    super::film::to_display(lin.z),
                );
                d = d * vignette((x as f32 + 0.5 - cx) / (pw * 0.5), (yc - cy) / (ph * 0.5));
                let g = hash01(x as u32, y as u32, grain_seed) - 0.5;
                d += V3::splat(g * GRAIN);
                img.px[y * w + x] = d * (fade_in * cover_x * cover_y);
            }
        }
    }
    img
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> Settings {
        Settings {
            pixel_aspect: 1.0,
            grain_seed: Some(1),
            fade_to: V3::ZERO,
        }
    }

    #[test]
    fn picture_covers_the_terminal() {
        // 16:9 terminal, 16:10 film: as wide as the terminal, a little off the top and bottom
        let (x0, y0, w, h) = picture_rect(160, 90, 1.0, 1.6);
        assert_eq!((x0, w), (0.0, 160.0));
        assert!((h - 100.0).abs() < 1e-3 && (y0 + 5.0).abs() < 1e-3);
        // a narrower terminal: as tall as it, the sides cropped
        let (x0, y0, w, h) = picture_rect(120, 100, 1.0, 1.6);
        assert_eq!((y0, h), (0.0, 100.0));
        assert!((w - 160.0).abs() < 1e-3 && (x0 + 20.0).abs() < 1e-3);
    }

    #[test]
    fn weights_cover_and_normalise() {
        for (n, src) in [(120usize, 320usize), (500, 320), (37, 134)] {
            let ws = weights(n, 0.0, n as f32, src);
            for (i, w) in ws.iter().enumerate() {
                let sum: f32 = w.iter().map(|p| p.1).sum();
                assert!((sum - 1.0).abs() < 1e-4, "pixel {i} sums to {sum}");
                assert!(w.iter().all(|&(s, _)| s < src));
            }
        }
    }

    #[test]
    fn flat_frame_stays_flat_and_fills_the_terminal() {
        let (sw, sh) = (32, 20);
        let grey = vec![V3::splat(0.2); sw * sh];
        let want = super::super::film::to_display(0.2);
        for (w, h) in [(60, 40), (90, 40), (40, 40)] {
            let img = compose(&grey, sw, sh, 5.0, w, h, &settings());
            let mid = img.get(w / 2, h / 2);
            assert!((mid.x - want).abs() < 0.03, "{w}x{h}: centre {mid:?} vs {want}");
            // no bars: every edge pixel shows the film (darkened by the vignette at most)
            for (x, y) in [(0, 0), (w - 1, 0), (w / 2, 0), (w / 2, h - 1), (0, h / 2), (w - 1, h / 2)] {
                assert!(img.get(x, y).x > want * 0.4, "{w}x{h}: ({x},{y}) {:?}", img.get(x, y));
            }
        }
    }

    #[test]
    fn fades_start_black_and_end_on_target() {
        let (sw, sh) = (32, 14);
        let grey = vec![V3::splat(0.5); sw * sh];
        let start = compose(&grey, sw, sh, 0.0, 40, 20, &settings());
        assert!(start.px.iter().all(|p| *p == V3::ZERO));
        let st = Settings {
            fade_to: v3(0.15, 0.15, 0.15),
            ..settings()
        };
        let mut end = compose(&grey, sw, sh, T::TOTAL, 40, 20, &st);
        finish(&mut end, st.fade_to, 1.0);
        assert!(end.px.iter().all(|p| (p.x - 0.15).abs() < 1e-5));
    }
}
