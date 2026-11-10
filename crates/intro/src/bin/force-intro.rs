//! force-intro binary: plays the startup animation, or exports stills / benchmarks for checking it.
//! Exit codes: 0 played, skipped or not shown; 130 Ctrl+C (128+n for signal n); 1 error; 2 usage.

use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

use force_intro::intro::output::{ColorMode, Encoder};

/// Like `eprintln!`/`eprint!`, but a closed stderr (the terminal hung up) is not a panic.
macro_rules! say {
    ($($t:tt)*) => {{
        use std::io::Write as _;
        let _ = writeln!(std::io::stderr(), $($t)*);
    }};
}
macro_rules! say_part {
    ($($t:tt)*) => {{
        use std::io::Write as _;
        let _ = write!(std::io::stderr(), $($t)*);
    }};
}
use force_intro::intro::{self, timing, AnimMode, FadeTo, Options, Outcome};

const USAGE: &str =
    "usage: force-intro [--animations full|subtle|off] [--color auto|truecolor|256|ascii]
                 [--fade-to default|RRGGBB] [--handoff] [--cell-aspect H/W]
                 [--min-size COLSxROWS] [--verbose]
                 [--quality 0-6] [--trace FILE] (development)
       force-intro --png FILE --at T[,T...] [--size COLSxROWS] [--scale N] [--color ...]
       force-intro --ansi T [--size COLSxROWS] [--color ...]
       force-intro --bench [--size COLSxROWS] [--color ...]
       force-intro --splash-texture FILE --texture-size WxH --frame LEFT,TOP,RIGHT,BOTTOM
                  [--film-aspect W/H]";

enum Mode {
    Play,
    Png {
        path: PathBuf,
        times: Vec<f32>,
        scale: usize,
    },
    Ansi(f32),
    Bench,
    /// The film's laptop-screen texture (see `splash::texture`).
    Texture {
        path: PathBuf,
        size: (usize, usize),
        frame: [f32; 4],
        /// The shape of the film being rendered (default: the embedded one's).
        aspect: f32,
    },
}

struct Args {
    mode: Mode,
    opts: Options,
    size: (usize, usize),
    verbose: bool,
}

fn parse_size(s: &str) -> Option<(usize, usize)> {
    let (c, r) = s.split_once('x')?;
    let (c, r) = (c.parse().ok()?, r.parse().ok()?);
    (c > 0 && r > 0).then_some((c, r))
}

fn parse_time(s: &str) -> Result<f32, String> {
    match s.trim().parse::<f32>() {
        Ok(t) if t.is_finite() && (0.0..=timing::TOTAL).contains(&t) => Ok(t),
        _ => Err(format!("bad time {s:?} (0..{})", timing::TOTAL)),
    }
}

fn parse_args() -> Result<Args, String> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut opts = Options::default();
    let mut mode = Mode::Play;
    let mut size = (120, 40);
    let mut times: Vec<f32> = Vec::new();
    let mut png: Option<PathBuf> = None;
    let mut scale = 6;
    let mut verbose = false;
    let mut texture: Option<PathBuf> = None;
    let mut texture_size = (1220, 1000);
    let mut film_aspect: Option<f32> = None;
    let mut frame: Option<[f32; 4]> = None;
    let mut i = 0;
    let value = |i: &mut usize, name: &str| -> Result<String, String> {
        *i += 1;
        argv.get(*i)
            .cloned()
            .ok_or_else(|| format!("{name} needs a value"))
    };
    while i < argv.len() {
        match argv[i].as_str() {
            "--animations" => opts.animations = AnimMode::parse(&value(&mut i, "--animations")?),
            "--color" => {
                let v = value(&mut i, "--color")?;
                opts.color = if v == "auto" {
                    None
                } else {
                    Some(ColorMode::parse(&v).ok_or_else(|| format!("bad --color {v:?}"))?)
                };
            }
            "--fade-to" => {
                let v = value(&mut i, "--fade-to")?;
                opts.fade_to = FadeTo::parse(&v).ok_or_else(|| format!("bad --fade-to {v:?}"))?;
            }
            "--handoff" => opts.handoff = true,
            "--cell-aspect" => {
                let v = value(&mut i, "--cell-aspect")?;
                opts.cell_aspect = match v.parse::<f32>() {
                    Ok(a) if a.is_finite() && (1.0..=4.0).contains(&a) => Some(a),
                    _ => return Err(format!("bad --cell-aspect {v:?} (1..4)")),
                };
            }
            "--min-size" => {
                let v = value(&mut i, "--min-size")?;
                let (c, r) = parse_size(&v).ok_or_else(|| format!("bad --min-size {v:?}"))?;
                opts.min_cols = c.min(u16::MAX as usize) as u16;
                opts.min_rows = r.min(u16::MAX as usize) as u16;
            }
            "--size" => {
                let v = value(&mut i, "--size")?;
                size = parse_size(&v)
                    .filter(|s| s.0 <= 1000 && s.1 <= 500)
                    .ok_or_else(|| format!("bad --size {v:?}"))?;
            }
            "--png" => png = Some(PathBuf::from(value(&mut i, "--png")?)),
            "--at" => {
                times = value(&mut i, "--at")?
                    .split(',')
                    .map(parse_time)
                    .collect::<Result<_, _>>()?;
            }
            "--scale" => {
                let v = value(&mut i, "--scale")?;
                scale = v
                    .parse()
                    .ok()
                    .filter(|s| (1..=32).contains(s))
                    .ok_or_else(|| format!("bad --scale {v:?}"))?;
            }
            "--ansi" => mode = Mode::Ansi(parse_time(&value(&mut i, "--ansi")?)?),
            "--bench" => mode = Mode::Bench,
            "--splash-texture" => texture = Some(PathBuf::from(value(&mut i, "--splash-texture")?)),
            "--film-aspect" => {
                let v = value(&mut i, "--film-aspect")?;
                film_aspect = match v.parse::<f32>() {
                    Ok(a) if a.is_finite() && (0.5..=4.0).contains(&a) => Some(a),
                    _ => return Err(format!("bad --film-aspect {v:?} (0.5..4)")),
                };
            }
            "--texture-size" => {
                let v = value(&mut i, "--texture-size")?;
                texture_size = parse_size(&v)
                    .filter(|s| s.0 <= 8192 && s.1 <= 8192)
                    .ok_or_else(|| format!("bad --texture-size {v:?}"))?;
            }
            "--frame" => {
                let v = value(&mut i, "--frame")?;
                let f: Vec<f32> = v.split(',').filter_map(|x| x.trim().parse().ok()).collect();
                match f[..] {
                    [l, t, r, b] if f.iter().all(|x| x.is_finite()) && r > l && b > t => frame = Some([l, t, r, b]),
                    _ => return Err(format!("bad --frame {v:?} (LEFT,TOP,RIGHT,BOTTOM)")),
                }
            }
            "--verbose" | "-v" => verbose = true,
            "--trace" => opts.trace = Some(PathBuf::from(value(&mut i, "--trace")?)),
            "--quality" => {
                let v = value(&mut i, "--quality")?;
                opts.quality = match v.parse::<usize>() {
                    Ok(q) if q < intro::term::QUALITY.len() => Some(q),
                    _ => return Err(format!("bad --quality {v:?} (0..{})", intro::term::QUALITY.len() - 1)),
                };
            }
            "--help" | "-h" => return Err(String::new()),
            other => return Err(format!("unknown argument: {other}")),
        }
        i += 1;
    }
    if let Some(path) = texture {
        let frame = frame.ok_or("--splash-texture needs --frame")?;
        mode = Mode::Texture {
            path,
            size: texture_size,
            frame,
            aspect: film_aspect.unwrap_or_else(intro::film::aspect),
        };
    } else if let Some(path) = png {
        if times.is_empty() {
            return Err("--png needs --at".into());
        }
        mode = Mode::Png { path, times, scale };
    }
    Ok(Args {
        mode,
        opts,
        size,
        verbose,
    })
}

/// Color mode for offline output: explicit, else truecolor (the environment is not a terminal).
fn offline_mode(opts: &Options) -> ColorMode {
    opts.color.unwrap_or(ColorMode::TrueColor)
}

fn export_png(a: &Args, path: &Path, times: &[f32], scale: usize) -> Result<(), String> {
    let mode = offline_mode(&a.opts);
    if mode == ColorMode::Ascii {
        return Err("--png shows pixels; use --ansi T --color ascii for the ASCII look".into());
    }
    let (cols, rows) = a.size;
    let per_row = times.len().min(3);
    let tiles: Vec<(usize, usize, Vec<u8>)> = times
        .iter()
        .map(|&t| {
            let img = intro::render_frame(t, false, cols, rows, mode, &a.opts);
            // half-block pixels are 1 cell wide and half a cell tall
            let pixel_h = (a.opts.cell_aspect() / 2.0).round().max(1.0) as usize;
            intro::preview_rgb(&img, mode, scale, pixel_h)
        })
        .collect();
    let (tw, th) = (tiles[0].0, tiles[0].1);
    let gap = 8;
    let n_rows = times.len().div_ceil(per_row);
    let (w, h) = (
        per_row * tw + (per_row - 1) * gap,
        n_rows * th + (n_rows - 1) * gap,
    );
    let mut sheet = vec![40u8; w * h * 3];
    for (k, (_, _, px)) in tiles.iter().enumerate() {
        let (ox, oy) = ((k % per_row) * (tw + gap), (k / per_row) * (th + gap));
        for y in 0..th {
            let src = &px[y * tw * 3..(y + 1) * tw * 3];
            let dst = ((oy + y) * w + ox) * 3;
            sheet[dst..dst + tw * 3].copy_from_slice(src);
        }
    }
    intro::png::write(path, w, h, &sheet).map_err(|e| format!("{}: {e}", path.display()))
}

fn print_ansi(a: &Args, t: f32) {
    let mode = offline_mode(&a.opts);
    let (cols, rows) = a.size;
    let img = intro::render_frame(t, false, cols, rows, mode, &a.opts);
    let enc = Encoder::new(mode, cols, rows);
    let mut out = Vec::new();
    enc.encode_lines(&img, &mut out);
    use std::io::Write;
    let _ = std::io::stdout().write_all(&out);
}

/// Render time and output size per frame at the full rate, at each quality level a terminal that
/// can't keep up is stepped through, and at the slow rate.
fn bench(a: &Args) {
    let mode = offline_mode(&a.opts);
    let (cols, rows) = a.size;
    let mut runs: Vec<(String, f32, bool, usize)> = intro::term::QUALITY
        .iter()
        .enumerate()
        .map(|(k, q)| (format!("quality {k}"), timing::FPS, q.still_grain, k))
        .collect();
    let worst = intro::term::QUALITY[intro::term::QUALITY.len() - 1];
    let _ = worst;
    runs.push(("slow rate".into(), timing::SLOW_FPS, true, intro::term::QUALITY.len() - 1));
    for (name, fps, still, level) in runs {
        let q = intro::term::QUALITY[level];
        let tol = q.tolerance;
        let mut enc = Encoder::new(mode, cols, rows);
        enc.set_tolerance(tol);
        let (mut total_ms, mut worst_ms, mut bytes, mut max_bytes) = (0.0f64, 0.0f64, 0usize, 0usize);
        let n = (timing::TOTAL * fps).round() as u64;
        let mut buf = Vec::new();
        for f in 0..=n {
            let t = f as f32 / fps;
            let start = Instant::now();
            let mut img = intro::render_frame(t, still, cols, rows, mode, &a.opts);
            if intro::splash::weight(t) < 1.0 {
                intro::term::coarsen(&mut img, mode.pixels_per_cell(), q);
            }
            let ms = start.elapsed().as_secs_f64() * 1000.0;
            buf.clear();
            enc.set_exact(intro::splash::weight(t) >= 1.0);
            enc.encode(&img, false, &mut buf);
            total_ms += ms;
            worst_ms = worst_ms.max(ms);
            bytes += buf.len();
            max_bytes = max_bytes.max(buf.len());
        }
        let frames = (n + 1) as f64;
        println!(
            "{cols}x{rows} {mode:?} {name:<10} {fps} fps, grain {}, tolerance {tol}{}{}: render avg {:.1} ms (worst {:.1}); output avg {:.1} KB/frame ({:.2} MB/s), max {:.1} KB",
            if still { "still" } else { "live" },
            if q.pairs { ", pairs" } else { "" },
            if q.merge { ", merged" } else { "" },
            total_ms / frames,
            worst_ms,
            bytes as f64 / frames / 1024.0,
            bytes as f64 / frames * fps as f64 / 1e6,
            max_bytes as f64 / 1024.0
        );
    }
}

fn main() -> ExitCode {
    let a = match parse_args() {
        Ok(a) => a,
        Err(e) => {
            if !e.is_empty() {
                say!("force-intro: {e}");
            }
            say!("{USAGE}");
            return ExitCode::from(2);
        }
    };
    match &a.mode {
        Mode::Png { path, times, scale } => match export_png(&a, path, times, *scale) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                say!("force-intro: {e}");
                ExitCode::from(1)
            }
        },
        Mode::Ansi(t) => {
            print_ansi(&a, *t);
            ExitCode::SUCCESS
        }
        Mode::Bench => {
            bench(&a);
            ExitCode::SUCCESS
        }
        Mode::Texture { path, size, frame, aspect } => {
            let rgb = intro::splash::texture(size.0, size.1, *frame, *aspect);
            match intro::png::write(path, size.0, size.1, &rgb) {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => {
                    say!("force-intro: {}: {e}", path.display());
                    ExitCode::from(1)
                }
            }
        }
        Mode::Play => match intro::run(&a.opts) {
            Ok(r) => {
                if a.verbose {
                    say_part!("force-intro: {:?}", r.outcome);
                    if r.frames > 0 {
                        say_part!(
                            "; {} frames in {:.2} s ({:.0} fps), {}, flow control {}",
                            r.frames,
                            r.seconds,
                            r.frames as f32 / r.seconds.max(1e-3),
                            match r.slow_from {
                                Some(t) => match r.slow_until {
                                    Some(u) => format!("down to {} fps from {t:.1} s, full rate again at {u:.1} s", r.min_fps),
                                    None => format!("down to {} fps from {t:.1} s", r.min_fps),
                                },
                                None => "full rate".into(),
                            },
                            if r.flow_control { "on" } else { "off (no answers)" },
                        );
                        match r.quality_from {
                            Some(t) => say_part!(", coarsest quality {} (from {t:.1} s)", r.quality),
                            None => say_part!(", quality {}", r.quality),
                        }
                        if r.level_frames.iter().skip(1).any(|&n| n > 0) {
                            let used: Vec<String> = r.level_frames.iter().enumerate()
                                .filter(|(_, &n)| n > 0).map(|(k, n)| format!("{k}:{n}")).collect();
                            say_part!(" [frames per level {}]", used.join(" "));
                        }
                        say_part!(
                            ", cell aspect {:.2} ({})",
                            r.cell_aspect,
                            if a.opts.cell_aspect.is_some() {
                                "given"
                            } else if r.cell_aspect_measured {
                                "measured"
                            } else {
                                "assumed"
                            }
                        );
                    }
                    say!();
                }
                match r.outcome {
                    Outcome::Interrupted(code) => ExitCode::from(code.clamp(1, 255) as u8),
                    _ => ExitCode::SUCCESS,
                }
            }
            Err(e) => {
                say!("force-intro: {e}");
                ExitCode::from(1)
            }
        },
    }
}
