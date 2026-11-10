//! The intro film: frames rendered by Blender (`blender/scene.py`), packed by `blender/pack.py`
//! into `assets/intro.film`, embedded in the binary and decoded here, one frame after the other.
//!
//! Format (little-endian): `b"FRCFILM1"`, u16 width, u16 height, u16 fps, u16 frame count,
//! u8 chroma step; then per frame a u32 length and a zlib stream of three planes, each the
//! difference (mod 256) from the previous frame's plane: Y (full size), Co and Cg (half size in
//! both directions, quantised by the chroma step).

use std::sync::{Mutex, OnceLock};

use super::math::{v3, V3};

static DATA: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/assets/intro.film"));

/// A parsed film (frame data still compressed).
#[derive(Debug)]
pub struct Film<'a> {
    pub w: usize,
    pub h: usize,
    pub fps: u16,
    chroma_step: u8,
    frames: Vec<&'a [u8]>,
}

impl<'a> Film<'a> {
    pub fn parse(data: &'a [u8]) -> Result<Film<'a>, String> {
        let head = data.get(..17).ok_or("film too short")?.split_at(8);
        if head.0 != b"FRCFILM1" {
            return Err("not a force-intro film".into());
        }
        let u16_at = |i: usize| u16::from_le_bytes([head.1[i], head.1[i + 1]]);
        let (w, h, fps, n) = (u16_at(0) as usize, u16_at(2) as usize, u16_at(4), u16_at(6));
        let chroma_step = head.1[8];
        if w == 0 || h == 0 || w % 2 == 1 || h % 2 == 1 || fps == 0 || n == 0 || chroma_step == 0 {
            return Err(format!("bad film header {w}x{h} {fps} fps {n} frames"));
        }
        let mut frames = Vec::with_capacity(n as usize);
        let mut at = 17;
        for i in 0..n {
            let len = data
                .get(at..at + 4)
                .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as usize)
                .ok_or_else(|| format!("frame {i} truncated"))?;
            frames.push(
                data.get(at + 4..at + 4 + len)
                    .ok_or_else(|| format!("frame {i} truncated"))?,
            );
            at += 4 + len;
        }
        Ok(Film {
            w,
            h,
            fps,
            chroma_step,
            frames,
        })
    }

    /// The film built into this binary.
    pub fn embedded() -> &'static Film<'static> {
        static FILM: OnceLock<Film<'static>> = OnceLock::new();
        FILM.get_or_init(|| Film::parse(DATA).expect("embedded film is valid"))
    }

    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }

    /// Width / height of the picture.
    pub fn aspect(&self) -> f32 {
        self.w as f32 / self.h as f32
    }
}

/// Width / height of the embedded film.
pub fn aspect() -> f32 {
    Film::embedded().aspect()
}

/// Decodes frames in order (each builds on the previous one); seeking backwards restarts.
pub struct Decoder<'a> {
    film: &'a Film<'a>,
    /// Index of the frame held in `planes`, if any.
    at: Option<usize>,
    planes: [Vec<u8>; 3],
}

impl<'a> Decoder<'a> {
    pub fn new(film: &'a Film<'a>) -> Decoder<'a> {
        let (y, c) = (film.w * film.h, (film.w / 2) * (film.h / 2));
        Decoder {
            film,
            at: None,
            planes: [vec![0; y], vec![0; c], vec![0; c]],
        }
    }

    /// Makes frame `i` (clamped to the film) current.
    pub fn seek(&mut self, i: usize) -> Result<(), String> {
        let i = i.min(self.film.frame_count() - 1);
        if self.at.is_some_and(|at| at > i) {
            self.at = None;
            for p in &mut self.planes {
                p.fill(0);
            }
        }
        while self.at != Some(i) {
            let next = self.at.map_or(0, |a| a + 1);
            let raw = miniz_oxide::inflate::decompress_to_vec_zlib(self.film.frames[next])
                .map_err(|e| format!("frame {next}: {e:?}"))?;
            let total: usize = self.planes.iter().map(Vec::len).sum();
            if raw.len() != total {
                return Err(format!(
                    "frame {next}: {} bytes, expected {total}",
                    raw.len()
                ));
            }
            let mut off = 0;
            for p in &mut self.planes {
                let n = p.len();
                for (v, d) in p.iter_mut().zip(&raw[off..off + n]) {
                    *v = v.wrapping_add(*d);
                }
                off += n;
            }
            self.at = Some(next);
        }
        Ok(())
    }

    /// The current frame as linear RGB, row-major `film.w` x `film.h`.
    pub fn linear(&self) -> Vec<V3> {
        let (w, h) = (self.film.w, self.film.h);
        let (cw, ch) = (w / 2, h / 2);
        let step = self.film.chroma_step as f32;
        let lut = srgb_lut();
        let mut out = Vec::with_capacity(w * h);
        for y in 0..h {
            // chroma sample positions sit at the centres of 2x2 blocks: bilinear in between
            let fy = ((y as f32 + 0.5) / 2.0 - 0.5).clamp(0.0, (ch - 1) as f32);
            let (y0, ty) = (fy.floor() as usize, fy.fract());
            let y1 = (y0 + 1).min(ch - 1);
            for x in 0..w {
                let fx = ((x as f32 + 0.5) / 2.0 - 0.5).clamp(0.0, (cw - 1) as f32);
                let (x0, tx) = (fx.floor() as usize, fx.fract());
                let x1 = (x0 + 1).min(cw - 1);
                let chroma = |p: &[u8]| {
                    let s = |xx: usize, yy: usize| p[yy * cw + xx] as f32;
                    let top = s(x0, y0) + (s(x1, y0) - s(x0, y0)) * tx;
                    let bot = s(x0, y1) + (s(x1, y1) - s(x0, y1)) * tx;
                    (top + (bot - top) * ty) * step - 128.0
                };
                let (co, cg) = (chroma(&self.planes[1]), chroma(&self.planes[2]));
                let luma = self.planes[0][y * w + x] as f32;
                let base = luma - cg;
                let to_lin = |c: f32| lut[c.round().clamp(0.0, 255.0) as usize];
                out.push(v3(to_lin(base + co), to_lin(luma + cg), to_lin(base - co)));
            }
        }
        out
    }
}

/// sRGB byte -> linear, once.
fn srgb_lut() -> &'static [f32; 256] {
    static LUT: OnceLock<[f32; 256]> = OnceLock::new();
    LUT.get_or_init(|| {
        let mut t = [0.0; 256];
        for (i, v) in t.iter_mut().enumerate() {
            let c = i as f32 / 255.0;
            *v = if c <= 0.04045 {
                c / 12.92
            } else {
                ((c + 0.055) / 1.055).powf(2.4)
            };
        }
        t
    })
}

/// Linear -> display sRGB (0..1).
pub fn to_display(c: f32) -> f32 {
    let c = c.clamp(0.0, 1.0);
    if c <= 0.003_130_8 {
        c * 12.92
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

/// The current frame of the embedded film at `index`, as linear RGB. Keeps one decoder for the
/// whole process, so playing forward decodes each frame once.
pub fn embedded_frame(index: usize) -> Vec<V3> {
    static DEC: OnceLock<Mutex<Decoder<'static>>> = OnceLock::new();
    let dec = DEC.get_or_init(|| Mutex::new(Decoder::new(Film::embedded())));
    let mut d = dec.lock().unwrap_or_else(|e| e.into_inner());
    d.seek(index).expect("embedded film decodes");
    d.linear()
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// Encodes RGB frames the way `blender/pack.py` does (for round-trip tests).
    pub fn encode(w: usize, h: usize, frames: &[Vec<[u8; 3]>], step: u8) -> Vec<u8> {
        let mut out = b"FRCFILM1".to_vec();
        for v in [w as u16, h as u16, 30, frames.len() as u16] {
            out.extend_from_slice(&v.to_le_bytes());
        }
        out.push(step);
        let mut prev: Option<[Vec<u8>; 3]> = None;
        for f in frames {
            let px = |x: usize, y: usize| {
                let p = f[y * w + x];
                (p[0] as f32, p[1] as f32, p[2] as f32)
            };
            let ypl: Vec<u8> = (0..w * h)
                .map(|i| {
                    let (r, g, b) = px(i % w, i / w);
                    (0.25 * r + 0.5 * g + 0.25 * b).round() as u8
                })
                .collect();
            let chroma = |which: usize| -> Vec<u8> {
                let mut c = Vec::new();
                for cy in 0..h / 2 {
                    for cx in 0..w / 2 {
                        let mut acc = 0.0;
                        for (dx, dy) in [(0, 0), (1, 0), (0, 1), (1, 1)] {
                            let (r, g, b) = px(cx * 2 + dx, cy * 2 + dy);
                            acc += if which == 0 {
                                0.5 * (r - b)
                            } else {
                                0.5 * g - 0.25 * (r + b)
                            };
                        }
                        let s = step as f32;
                        c.push((acc / 4.0 / s + 128.0 / s).round().clamp(0.0, 255.0) as u8);
                    }
                }
                c
            };
            let cur = [ypl, chroma(0), chroma(1)];
            let mut body = Vec::new();
            for (k, p) in cur.iter().enumerate() {
                for (i, v) in p.iter().enumerate() {
                    let base = prev.as_ref().map_or(0, |q| q[k][i]);
                    body.push(v.wrapping_sub(base));
                }
            }
            let z = miniz_oxide::deflate::compress_to_vec_zlib(&body, 6);
            out.extend_from_slice(&(z.len() as u32).to_le_bytes());
            out.extend_from_slice(&z);
            prev = Some(cur);
        }
        out
    }

    #[test]
    fn round_trip_keeps_colours_close() {
        let (w, h) = (8, 6);
        let frame = |shift: u8| -> Vec<[u8; 3]> {
            (0..w * h)
                .map(|i| {
                    let (x, y) = ((i % w) as u8, (i / w) as u8);
                    [40 + x * 10 + shift, 20 + y * 12, 90 - x * 5]
                })
                .collect()
        };
        let frames = [frame(0), frame(7), frame(3)];
        let data = encode(w, h, &frames, 2);
        let film = Film::parse(&data).unwrap();
        assert_eq!((film.w, film.h, film.frame_count()), (w, h, 3));
        let mut dec = Decoder::new(&film);
        // forward, then backward (restart), then forward again
        for i in [0usize, 1, 2, 1, 2, 0] {
            dec.seek(i).unwrap();
            let px = dec.linear();
            for (got, want) in px.iter().zip(&frames[i]) {
                let back = |c: f32| to_display(c) * 255.0;
                for (g, w) in [
                    (back(got.x), want[0]),
                    (back(got.y), want[1]),
                    (back(got.z), want[2]),
                ] {
                    assert!((g - w as f32).abs() < 9.0, "frame {i}: {g} vs {w}");
                }
            }
        }
    }

    #[test]
    fn rejects_bad_data() {
        assert!(Film::parse(b"nope").is_err());
        let mut data = encode(2, 2, &[vec![[1, 2, 3]; 4]], 2);
        data.truncate(data.len() - 1);
        assert!(Film::parse(&data).is_err(), "truncated frame");
        let mut bad = encode(2, 2, &[vec![[1, 2, 3]; 4]], 2);
        bad[8] = 3; // odd width
        assert!(Film::parse(&bad).is_err());
    }

    #[test]
    fn embedded_film_matches_the_timing() {
        let film = Film::embedded();
        assert_eq!(film.fps as f32, crate::intro::timing::FPS);
        assert_eq!(
            film.frame_count(),
            crate::intro::timing::FILM_FRAMES
        );
        assert!(
            (film.aspect() - 1.6).abs() < 0.03,
            "aspect {}",
            film.aspect()
        );
        // first and last frames decode
        let last = embedded_frame(film.frame_count() - 1);
        assert_eq!(last.len(), film.w * film.h);
        let first = embedded_frame(0);
        assert!(first.iter().all(|p| p.is_finite()));
    }
}
