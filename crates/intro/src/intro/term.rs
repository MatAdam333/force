//! Terminal playback: setup and guaranteed restore (normal end, skip, Ctrl+C, signals, errors and
//! panics), the real-time frame loop with flow control, input, and resize handling.
//!
//! Flow control: every frame is followed by a Primary Device Attributes query (`ESC [ c`). A
//! terminal answers it only once it has processed everything before it, so the answers say how
//! far behind it is, locally or over ssh (where writes never block: ssh buffers megabytes). The
//! frames allowed unanswered are what one round trip holds at the frame rate (the shortest round
//! trip seen) plus `MARGIN`: a link's latency costs no frames, only the picture arrives one round
//! trip late. A frame that falls due while the terminal is further behind than that is dropped, so
//! the timeline always runs in real time and the picture is at most a round trip and a few frames
//! late.
//!
//! Rate control: when the terminal or link is the bottleneck (frames are being dropped), what it
//! processed in the last second is its capacity, and each frame gets that per frame as its byte
//! budget: it is encoded at the current [`QUALITY`] level and, if it doesn't fit, again one level
//! coarser until it does, up to the coarsest level its frame-rate tier allows ([`TIERS`]: at 60,
//! 30 and 20 fps only full-resolution levels, still grain and coarser tolerances; the film at half
//! resolution only as a last resort at 20 fps). The level gets finer again once frames would fit
//! at the finer one with room to spare, and the capacity estimate grows while nothing is dropped.
//! A tier is left for the next lower rate when its coarsest level hasn't kept up for `STEP_HOLD`,
//! and returned to when frames would fit at the higher rate; the native splash (always exact)
//! gets the full rate again. A terminal that never answers is played without flow control;
//! its blocking writes show up as dropped frames, so the same rate control applies.

use std::collections::VecDeque;
use std::io::{self, IsTerminal, Write};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::Once;
use std::time::{Duration, Instant};

use crossterm::terminal;

use super::output::{Color, ColorMode, Encoder};
use super::timing as T;
use super::{render_frame, FadeTo, Options, Outcome, Report};

/// True while the terminal is in raw mode / alternate screen, so the panic hook can restore it.
static ACTIVE: AtomicBool = AtomicBool::new(false);
/// Last caught signal number (0 = none).
static SIGNAL: AtomicI32 = AtomicI32::new(0);
/// Set by SIGWINCH.
static RESIZED: AtomicBool = AtomicBool::new(false);
static PANIC_HOOK: Once = Once::new();

/// Undo everything `enter` did except raw mode: attributes, autowrap, cursor, alternate screen.
const RESTORE: &str = "\x1b[0m\x1b[?2026l\x1b[?7h\x1b[?25h\x1b[?1049l";
const RESTORE_KEEP_SCREEN: &str = "\x1b[0m\x1b[?2026l\x1b[?7h\x1b[?25h";

extern "C" fn on_signal(sig: libc::c_int) {
    if sig == libc::SIGWINCH {
        RESIZED.store(true, Ordering::SeqCst);
    } else {
        SIGNAL.store(sig, Ordering::SeqCst);
    }
}

const SIGNALS: [libc::c_int; 5] = [
    libc::SIGINT,
    libc::SIGTERM,
    libc::SIGHUP,
    libc::SIGQUIT,
    libc::SIGWINCH,
];

/// Owns the terminal while the animation plays; restores it on drop no matter how we leave.
struct Guard {
    old_handlers: Vec<(libc::c_int, libc::sighandler_t)>,
    done: bool,
}

impl Guard {
    fn enter() -> io::Result<Guard> {
        PANIC_HOOK.call_once(|| {
            let prev = std::panic::take_hook();
            std::panic::set_hook(Box::new(move |info| {
                if ACTIVE.swap(false, Ordering::SeqCst) {
                    let mut out = io::stdout();
                    let _ = out.write_all(RESTORE.as_bytes());
                    let _ = out.flush();
                    let _ = terminal::disable_raw_mode();
                }
                prev(info);
            }));
        });
        SIGNAL.store(0, Ordering::SeqCst);
        RESIZED.store(false, Ordering::SeqCst);
        let handler = on_signal as extern "C" fn(libc::c_int) as libc::sighandler_t;
        let old_handlers = SIGNALS
            .iter()
            .map(|&s| (s, unsafe { libc::signal(s, handler) }))
            .collect();
        let mut g = Guard {
            old_handlers,
            done: false,
        };
        terminal::enable_raw_mode()?;
        ACTIVE.store(true, Ordering::SeqCst);
        // alternate screen, hide cursor, no autowrap (writing the last column must not scroll), clear
        let mut out = io::stdout();
        if let Err(e) = out
            .write_all(b"\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[0m\x1b[2J")
            .and_then(|_| out.flush())
        {
            g.restore(false);
            return Err(e);
        }
        Ok(g)
    }

    /// Restore the terminal. `keep_screen` leaves the alternate screen up (already painted in the
    /// hand-off colour) for an app that takes it over immediately.
    fn restore(&mut self, keep_screen: bool) {
        if self.done {
            return;
        }
        self.done = true;
        let mut out = io::stdout();
        let _ = out.write_all(
            if keep_screen {
                RESTORE_KEEP_SCREEN
            } else {
                RESTORE
            }
            .as_bytes(),
        );
        let _ = out.flush();
        let _ = terminal::disable_raw_mode();
        ACTIVE.store(false, Ordering::SeqCst);
        for (s, h) in self.old_handlers.drain(..) {
            unsafe {
                libc::signal(s, h);
            }
        }
    }
}

impl Drop for Guard {
    fn drop(&mut self) {
        self.restore(false);
    }
}

/// Cell height / width from the terminal's size in pixels, where it reports one (TIOCGWINSZ; ssh
/// passes it on). Implausible values are ignored.
fn measured_cell_aspect() -> Option<f32> {
    let mut ws: libc::winsize = unsafe { std::mem::zeroed() };
    if unsafe { libc::ioctl(libc::STDOUT_FILENO, libc::TIOCGWINSZ, &mut ws) } != 0 {
        return None;
    }
    let (cols, rows, xp, yp) = (ws.ws_col, ws.ws_row, ws.ws_xpixel, ws.ws_ypixel);
    if cols == 0 || rows == 0 || xp == 0 || yp == 0 {
        return None;
    }
    let a = (yp as f32 / rows as f32) / (xp as f32 / cols as f32);
    (a.is_finite() && (1.0..=4.0).contains(&a)).then_some(a)
}

fn too_small(cols: u16, rows: u16, opts: &Options) -> bool {
    cols < opts.min_cols || rows < opts.min_rows
}

/// Why the loop ended.
enum End {
    Completed,
    Skipped,
    Interrupted(i32),
}

/// Paint the whole screen in the hand-off colour (background colour erase).
fn paint_handoff(out: &mut impl Write, mode: ColorMode, fade: FadeTo) -> io::Result<()> {
    let bg = match (fade, mode) {
        (FadeTo::Default, _) | (_, ColorMode::Ascii) => Color::Default,
        (FadeTo::Rgb(r, g, b), ColorMode::TrueColor) => Color::Rgb(r, g, b),
        (FadeTo::Rgb(r, g, b), ColorMode::Ansi256) => {
            Color::Idx(super::output::to_256(fade_rgb(r, g, b), 0.0))
        }
    };
    let seq = match bg {
        Color::Rgb(r, g, b) => format!("\x1b[48;2;{r};{g};{b}m\x1b[2J\x1b[H"),
        Color::Idx(i) => format!("\x1b[48;5;{i}m\x1b[2J\x1b[H"),
        Color::Default => "\x1b[0m\x1b[2J\x1b[H".to_string(),
    };
    out.write_all(seq.as_bytes())?;
    out.flush()
}

fn fade_rgb(r: u8, g: u8, b: u8) -> super::math::V3 {
    super::math::v3(r as f32 / 255.0, g as f32 / 255.0, b as f32 / 255.0)
}

/// One step of the quality ladder: what a terminal or link that can't keep up gives up, in order,
/// before it gets fewer frames.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Quality {
    /// Film grain held still (it changes most cells ten times a second otherwise).
    pub still_grain: bool,
    /// The encoder's tolerance (levels per channel a cell may be off before it is rewritten).
    pub tolerance: u8,
    /// The film (never the native splash) at half resolution: horizontal neighbours share their
    /// colours (`pairs`: the second cell of a pair needs no colour codes), and each cell is one
    /// colour (`merge`: background codes only).
    pub pairs: bool,
    pub merge: bool,
}

/// The quality ladder, best first.
pub const QUALITY: [Quality; 7] = [
    Quality { still_grain: false, tolerance: 2, pairs: false, merge: false },
    Quality { still_grain: true, tolerance: 2, pairs: false, merge: false },
    Quality { still_grain: true, tolerance: 4, pairs: false, merge: false },
    Quality { still_grain: true, tolerance: 6, pairs: false, merge: false },
    Quality { still_grain: true, tolerance: 9, pairs: false, merge: false },
    Quality { still_grain: true, tolerance: 6, pairs: false, merge: true },
    Quality { still_grain: true, tolerance: 6, pairs: true, merge: true },
];

/// The film's pixels at the coarser resolution of `q` (in place; text cells are untouched).
pub fn coarsen(img: &mut super::grade::Image, ppc: usize, q: Quality) {
    let w = img.w;
    if q.merge && ppc == 2 {
        for r in 0..img.h / 2 {
            for x in 0..w {
                let (a, b) = (2 * r * w + x, (2 * r + 1) * w + x);
                let m = (img.px[a] + img.px[b]) * 0.5;
                (img.px[a], img.px[b]) = (m, m);
            }
        }
    }
    if q.pairs {
        for y in 0..img.h {
            for x in (0..w.saturating_sub(1)).step_by(2) {
                let (a, b) = (y * w + x, y * w + x + 1);
                let m = (img.px[a] + img.px[b]) * 0.5;
                (img.px[a], img.px[b]) = (m, m);
            }
        }
    }
}

/// Estimated output bytes of each quality level relative to the best (`--bench`, 120x40 and
/// 191x47 agree within a few percent).
const QUALITY_BYTES: [f32; 7] = [1.0, 0.93, 0.65, 0.5, 0.38, 0.3, 0.2];
/// Rate control (a terminal or link that can't keep up): each frame is encoded at the current
/// quality level and, if it doesn't fit the byte budget (what they carried per frame while they
/// were the bottleneck), again one level coarser until it fits. The level only gets finer again
/// after this many frames would have fitted at the finer one with room to spare ...
const FINER_AFTER: u32 = 30;
/// ... "room to spare" meaning under this share of the budget.
const FINER_MARGIN: f32 = 0.8;
/// Ticks dropped in the last half second at or above this: the link or terminal is the bottleneck,
/// and what they processed in the last second is the capacity.
const LIMITED_DROPS: u64 = 4;
/// While nothing is dropped, the capacity estimate grows by this share per second (so a link that
/// gets faster is found again).
const PROBE_PER_S: f32 = 0.3;
/// The coarsest level of a tier must have been needed this long before the frame rate drops.
const STEP_HOLD: Duration = Duration::from_millis(1000);
/// Rate tiers, in the order a terminal or link that can't keep up is stepped through: frame rate
/// and the coarsest quality level allowed. Resolution is the last thing given up (the owner notices
/// a pixelated picture more than fewer frames): first fewer bytes at full resolution (levels 0-4),
/// then fewer frames at full resolution, and only at the lowest rate the film at half resolution.
/// 20 fps divides the display's 60 Hz evenly, so its cadence stays steady.
pub const TIERS: [(f32, usize); 4] = [(60.0, 4), (30.0, 4), (20.0, 4), (20.0, 6)];
/// A tier is left (to fewer frames) when the terminal processed fewer than this share of its frame
/// rate in the last second while its coarsest level was needed ...
const TIER_DOWN_BELOW: f32 = 0.85;
/// ... and returned to (more frames) after this long of frames that would fit at the higher rate
/// with room to spare.
const TIER_UP_AFTER: Duration = Duration::from_millis(1000);

/// Unanswered frames allowed beyond what one round trip holds.
const MARGIN: usize = 2;
/// Unanswered frames allowed at most, and before the first round trip is measured.
const MAX_IN_FLIGHT: usize = 40;
const MIN_IN_FLIGHT: usize = 3;
/// Asks the terminal for its attributes; every terminal answers `ESC [ ? ... c`.
const QUERY: &[u8] = b"\x1b[c";
/// No answer to the first query in this time: the terminal doesn't answer, play without.
const FIRST_ANSWER_WAIT: Duration = Duration::from_millis(1000);
/// An answer this late (or three round trips, if longer) is given up on, so playback can't stall.
const ANSWER_GIVE_UP: Duration = Duration::from_millis(1000);
/// The start of an answer (or a lone Esc) left this long without its end is a key press.
const PARTIAL_INPUT: Duration = Duration::from_millis(200);
/// At the end, wait at most this long for the outstanding answers, so none of them reaches the
/// program that runs next as typed input.
const DRAIN: Duration = Duration::from_millis(1000);
/// Rate control and frame-rate tiers judge the terminal only after this much of the intro (and a
/// round trip): the first frames are tiny.
const RATE_WARMUP: Duration = Duration::from_millis(1500);

/// What arrived on stdin.
#[derive(Debug, Default, PartialEq, Eq)]
struct Input {
    /// Answers to [`QUERY`].
    answers: usize,
    /// Anything else: a key press.
    key: bool,
    /// Ctrl+C (raw mode delivers it as a byte).
    ctrl_c: bool,
}

/// Takes the complete answers and key presses out of `buf`, leaving the start of an unfinished
/// answer (or a lone Esc, which may be one) in it.
fn parse_input(buf: &mut Vec<u8>) -> Input {
    let mut res = Input::default();
    let mut i = 0;
    while i < buf.len() {
        if buf[i] != 0x1b {
            res.key = true;
            res.ctrl_c |= buf[i] == 0x03;
            i += 1;
            continue;
        }
        // ESC [ ? <digits and ;> c
        let mut j = i + 1;
        let mut complete = false;
        let mut matches = true;
        for want in *b"[?" {
            match buf.get(j) {
                None => break,
                Some(&b) if b == want => j += 1,
                Some(_) => {
                    matches = false;
                    break;
                }
            }
        }
        if matches && j == i + 3 {
            while j < buf.len() && (buf[j].is_ascii_digit() || buf[j] == b';') {
                j += 1;
            }
            match buf.get(j) {
                Some(b'c') => complete = true,
                Some(_) => matches = false,
                None => {}
            }
        }
        if !matches {
            res.key = true; // Esc, or a key's escape sequence
            i += 1;
        } else if complete {
            res.answers += 1;
            i = j + 1;
        } else {
            break; // unfinished: wait for the rest
        }
    }
    buf.drain(..i);
    res
}

/// Waits up to `timeout` for stdin to become readable, then reads what is there into `buf`.
fn read_input(buf: &mut Vec<u8>, timeout: Duration) -> io::Result<()> {
    let mut fds = libc::pollfd {
        fd: libc::STDIN_FILENO,
        events: libc::POLLIN,
        revents: 0,
    };
    let ms = timeout.as_micros().div_ceil(1000).min(i32::MAX as u128) as libc::c_int;
    let n = unsafe { libc::poll(&mut fds, 1, ms) };
    if n < 0 {
        let e = io::Error::last_os_error();
        return if e.kind() == io::ErrorKind::Interrupted {
            Ok(()) // a signal: the loop looks at it
        } else {
            Err(e)
        };
    }
    if n == 0 {
        return Ok(());
    }
    if fds.revents & (libc::POLLERR | libc::POLLNVAL) != 0 {
        return Err(io::Error::other("stdin closed"));
    }
    let mut chunk = [0u8; 1024];
    let r = unsafe { libc::read(libc::STDIN_FILENO, chunk.as_mut_ptr().cast(), chunk.len()) };
    match r {
        0 => Err(io::Error::other("stdin closed")),
        r if r > 0 => {
            buf.extend_from_slice(&chunk[..r as usize]);
            Ok(())
        }
        _ => {
            let e = io::Error::last_os_error();
            if matches!(e.kind(), io::ErrorKind::Interrupted | io::ErrorKind::WouldBlock) {
                Ok(())
            } else {
                Err(e)
            }
        }
    }
}

/// Flow control state: the frames on their way to the terminal.
struct Flow {
    /// Send times and sizes of the unanswered frames, oldest first.
    pending: VecDeque<(Instant, usize)>,
    /// The terminal answers queries (until shown otherwise).
    answers: bool,
    answered_any: bool,
    /// The shortest round trip seen (send to answer): the link's latency.
    min_rtt: Option<Duration>,
    /// When frames were processed (answered; sent, without answers) and their sizes, for the last
    /// second's rate and throughput, counted since `counting`.
    done: VecDeque<(Instant, usize)>,
    counting: Instant,
    /// Unfinished input, and since when.
    input: Vec<u8>,
    partial_since: Option<Instant>,
}

impl Flow {
    fn new() -> Flow {
        Flow {
            pending: VecDeque::new(),
            answers: true,
            answered_any: false,
            min_rtt: None,
            done: VecDeque::new(),
            counting: Instant::now(),
            input: Vec::new(),
            partial_since: None,
        }
    }

    /// Unanswered frames allowed at `fps`: a round trip's worth plus the margin.
    fn window(&self, fps: f32) -> usize {
        match self.min_rtt {
            None => MIN_IN_FLIGHT,
            Some(rtt) => ((rtt.as_secs_f32() * fps).ceil() as usize + MARGIN)
                .clamp(MIN_IN_FLIGHT, MAX_IN_FLIGHT),
        }
    }

    fn may_send(&self, fps: f32) -> bool {
        !self.answers || self.pending.len() < self.window(fps)
    }

    fn sent(&mut self, now: Instant, bytes: usize) {
        if self.answers {
            self.pending.push_back((now, bytes));
        } else {
            self.processed(now, bytes);
        }
    }

    fn processed(&mut self, now: Instant, bytes: usize) {
        self.done.push_back((now, bytes));
        while self.done.front().is_some_and(|&(d, _)| now - d > Duration::from_secs(1)) {
            self.done.pop_front();
        }
    }

    /// Frames processed in the second before `now`, once a whole second has been counted.
    fn rate(&self, now: Instant) -> Option<usize> {
        (now - self.counting >= Duration::from_secs(1))
            .then(|| self.done.iter().filter(|&&(d, _)| now - d <= Duration::from_secs(1)).count())
    }

    /// Bytes the terminal processed in the second before `now` (what the link and terminal carry
    /// while they are the bottleneck), once a whole second has been counted.
    fn throughput(&self, now: Instant) -> Option<usize> {
        (now - self.counting >= Duration::from_secs(1)).then(|| {
            self.done.iter().filter(|&&(d, _)| now - d <= Duration::from_secs(1)).map(|&(_, b)| b).sum()
        })
    }

    fn give_up_after(&self) -> Duration {
        if !self.answered_any {
            return FIRST_ANSWER_WAIT;
        }
        ANSWER_GIVE_UP.max(self.min_rtt.unwrap_or_default() * 3)
    }

    /// Reads input for up to `timeout`; returns what it was apart from answers.
    fn poll(&mut self, timeout: Duration) -> io::Result<Input> {
        read_input(&mut self.input, timeout)?;
        let mut got = parse_input(&mut self.input);
        let now = Instant::now();
        for _ in 0..got.answers {
            if let Some((sent, bytes)) = self.pending.pop_front() {
                let rtt = now - sent;
                self.min_rtt = Some(self.min_rtt.map_or(rtt, |m| m.min(rtt)));
                self.processed(now, bytes);
            }
            self.answered_any = true;
        }
        if self.input.is_empty() {
            self.partial_since = None;
        } else if now - *self.partial_since.get_or_insert(now) >= PARTIAL_INPUT {
            self.input.clear();
            self.partial_since = None;
            got.key = true;
        }
        if let Some(&(oldest, _)) = self.pending.front() {
            if now - oldest >= self.give_up_after() {
                if self.answered_any {
                    self.pending.pop_front();
                } else {
                    self.answers = false; // never answers: play without flow control
                    self.pending.clear();
                    self.done.clear();
                    self.counting = now;
                }
            }
        }
        Ok(got)
    }

    /// When the next answer is given up on (for the wait while the window is full).
    fn deadline(&self) -> Option<Instant> {
        Some(self.pending.front()?.0 + self.give_up_after())
    }

    /// Waits for the outstanding answers (at most `limit`), then drops any input left over.
    fn drain(&mut self, limit: Duration) {
        let until = Instant::now() + limit;
        while self.answers && !self.pending.is_empty() {
            let now = Instant::now();
            if now >= until || self.poll(until - now).is_err() {
                break;
            }
        }
        unsafe {
            libc::tcflush(libc::STDIN_FILENO, libc::TCIFLUSH);
        }
    }
}

/// Plays the animation on the controlling terminal. See [`Outcome`] for the results.
pub fn play(opts: &Options) -> io::Result<Report> {
    let not_shown = |why| Ok(Report::from(Outcome::NotShown(why)));
    if opts.animations == super::AnimMode::Off {
        return not_shown("animations are off");
    }
    if !io::stdout().is_terminal() || !io::stdin().is_terminal() {
        return not_shown("not a terminal");
    }
    let (mut cols, mut rows) = terminal::size()?;
    if too_small(cols, rows, opts) {
        return not_shown("terminal too small");
    }
    let mode = opts
        .color
        .unwrap_or_else(|| ColorMode::detect(|k| std::env::var(k).ok()));
    // the cell shape: given, else measured (again after a resize: the font may differ)
    let measure = opts.cell_aspect.is_none();
    let mut opts = opts.clone();
    if measure {
        opts.cell_aspect = measured_cell_aspect();
    }
    let measured = measure && opts.cell_aspect.is_some();
    let opts = &mut opts;

    let mut guard = Guard::enter()?;
    let mut enc = Encoder::new(mode, cols as usize, rows as usize);
    let mut buf: Vec<u8> = Vec::with_capacity(1 << 20);
    let mut flow = Flow::new();
    let mut tier = 0usize;
    let mut fps = TIERS[tier].0;
    let mut min_fps = fps;
    let mut up_since: Option<Instant> = None;
    let (mut frames, mut slow_from, mut slow_until) = (0usize, None, None);
    // rate control: the quality level (fixed by --quality, for development), the link's capacity
    // in bytes per second once it has been the bottleneck, the ticks dropped recently
    let adapt = opts.quality.is_none();
    let mut level = opts.quality.unwrap_or(0).min(QUALITY.len() - 1);
    let (mut worst_level, mut quality_from) = (level, None);
    let mut capacity: Option<f32> = None;
    let mut finer = 0u32;
    let mut level_frames = [0usize; QUALITY.len()];
    let mut trace: Vec<String> = Vec::new();
    let mut dropped: VecDeque<(Instant, u64)> = VecDeque::new();
    // since when every frame has needed the coarsest level (for the fall back to fewer frames)
    let mut coarsest_since: Option<Instant> = None;
    enc.set_tolerance(QUALITY[level].tolerance);
    let start = Instant::now();
    let mut stdout = io::stdout();

    let end: io::Result<End> = (|| {
        // the next frame is due at this tick of `fps`
        let mut due: u64 = 0;
        loop {
            if let sig @ 1.. = SIGNAL.load(Ordering::SeqCst) {
                return Ok(End::Interrupted(128 + sig));
            }
            if RESIZED.swap(false, Ordering::SeqCst) {
                let (c, r) = terminal::size()?;
                if too_small(c, r, opts) {
                    return Ok(End::Skipped);
                }
                if measure {
                    opts.cell_aspect = measured_cell_aspect();
                }
                if (c, r) != (cols, rows) {
                    (cols, rows) = (c, r);
                    enc = Encoder::new(mode, cols as usize, rows as usize);
                    stdout.write_all(b"\x1b[0m\x1b[2J")?;
                }
            }
            let now = Instant::now();
            let elapsed = (now - start).as_secs_f32();
            let tick = (elapsed * fps) as u64;
            if flow.may_send(fps) && tick >= due {
                let last = elapsed >= T::TOTAL;
                let t = if last { T::TOTAL } else { tick as f32 / fps };
                if tick > due {
                    dropped.push_back((now, tick - due));
                }
                while dropped.front().is_some_and(|&(d, _)| now - d > Duration::from_millis(500)) {
                    dropped.pop_front();
                }
                // the native splash is drawn exactly and at full resolution, whatever the budget
                let native = super::splash::weight(t) >= 1.0;
                let budget = capacity.filter(|_| adapt && !native).map(|c| c / fps);
                let still = QUALITY[level].still_grain;
                let frame = render_frame(t, still, cols as usize, rows as usize, mode, opts);
                // the coarsest level this tier allows (a level fixed with --quality is kept)
                let worst = if adapt { TIERS[tier].1 } else { level };
                let mut l = level;
                let size = loop {
                    let mut img = frame.clone();
                    if !native {
                        coarsen(&mut img, mode.pixels_per_cell(), QUALITY[l]);
                    }
                    let mut e = enc.clone();
                    e.set_tolerance(QUALITY[l].tolerance);
                    e.set_exact(native);
                    buf.clear();
                    buf.extend_from_slice(b"\x1b[?2026h");
                    e.encode(&img, last && opts.fade_to == FadeTo::Default, &mut buf);
                    let size = buf.len();
                    if budget.is_none_or(|b| size as f32 <= b) || l == worst {
                        enc = e;
                        break size;
                    }
                    l += 1;
                };
                buf.extend_from_slice(b"\x1b[?2026l");
                if flow.answers {
                    buf.extend_from_slice(QUERY);
                }
                stdout.write_all(&buf)?;
                stdout.flush()?;
                flow.sent(Instant::now(), buf.len());
                frames += 1;
                level_frames[if native { 0 } else { l }] += 1;
                if opts.trace.is_some() {
                    trace.push(format!("{t:.3} {} {} {size}\n", if native { 0 } else { l }, fps as u32));
                }
                if last {
                    return Ok(End::Completed);
                }
                due = tick + 1;
                // the level for the next frames: coarser at once; finer after a while of frames
                // that would have fitted at the finer level with room to spare
                if l > level {
                    (level, finer) = (l, 0);
                } else if let Some(b) = budget.filter(|_| level > 0) {
                    let predicted = size as f32 * QUALITY_BYTES[level - 1] / QUALITY_BYTES[level];
                    finer = if predicted < b * FINER_MARGIN { finer + 1 } else { 0 };
                    if finer >= FINER_AFTER {
                        (level, finer) = (level - 1, 0);
                    }
                }
                if level > worst_level {
                    (worst_level, quality_from) = (level, Some(elapsed));
                }
                // the capacity: measured while the link or terminal is the bottleneck, probed
                // upward while it isn't
                if let Some(carried) = flow.throughput(now).filter(|_| now - start >= RATE_WARMUP) {
                    let drops: u64 = dropped.iter().map(|&(_, n)| n).sum();
                    if drops >= LIMITED_DROPS {
                        let c = carried as f32;
                        capacity = Some(capacity.map_or(c, |old| 0.5 * old + 0.5 * c));
                    } else if let Some(c) = capacity.as_mut() {
                        *c *= 1.0 + PROBE_PER_S / fps;
                    }
                }
                // the frame rate: the native splash needs few bytes, so it gets the full rate again
                // (and steps down again later if even that doesn't keep up)
                let rtt = flow.min_rtt.unwrap_or_default();
                let mut new_tier = tier;
                if native && tier > 0 && slow_until.is_none() {
                    new_tier = 0;
                    slow_until = Some(elapsed);
                } else if !native {
                    // fewer frames when this tier's coarsest level hasn't kept up for a while
                    coarsest_since = if level >= worst { coarsest_since.or(Some(now)) } else { None };
                    let held = coarsest_since.is_some_and(|c| now - c >= STEP_HOLD + rtt);
                    let behind = flow.rate(now).is_some_and(|r| (r as f32) < TIER_DOWN_BELOW * fps);
                    if tier + 1 < TIERS.len() && held && behind && now - start >= RATE_WARMUP + rtt {
                        new_tier = tier + 1;
                    } else if tier > 0 && adapt {
                        // more frames when they would fit at the higher rate with room to spare (a
                        // frame's changes grow with the time between frames)
                        let up = TIERS[tier - 1].0;
                        let fits = capacity.is_some_and(|c| size as f32 * fps / up * 1.2 < c / up * FINER_MARGIN);
                        up_since = if fits { up_since.or(Some(now)) } else { None };
                        if up_since.is_some_and(|u| now - u >= TIER_UP_AFTER + rtt) {
                            new_tier = tier - 1;
                        }
                    }
                }
                if new_tier != tier {
                    tier = new_tier;
                    fps = TIERS[tier].0;
                    due = (elapsed * fps) as u64 + 1;
                    (coarsest_since, up_since) = (None, None);
                    if adapt {
                        level = level.min(TIERS[tier].1);
                    }
                    if fps < min_fps {
                        min_fps = fps;
                        slow_from = Some(elapsed);
                    }
                }
            }
            // wait for the next frame (or, while the terminal is behind, for its answers),
            // reacting to input meanwhile
            let now = Instant::now();
            let next = if flow.may_send(fps) {
                start + Duration::from_secs_f32(due as f32 / fps)
            } else {
                flow.deadline().unwrap_or(now)
            };
            let got = flow.poll(next.saturating_duration_since(now))?;
            if got.ctrl_c {
                return Ok(End::Interrupted(130));
            }
            if got.key {
                return Ok(End::Skipped);
            }
        }
    })();

    if let Some(path) = &opts.trace {
        let _ = std::fs::write(path, trace.concat());
    }
    // answers still on their way would otherwise reach the next program as typed input
    match end {
        Ok(End::Completed) | Ok(End::Skipped) | Ok(End::Interrupted(130)) => flow.drain(DRAIN),
        Ok(End::Interrupted(_)) => flow.drain(Duration::from_millis(200)),
        Err(_) => {}
    }
    let outcome = match end {
        Ok(End::Completed) | Ok(End::Skipped) => {
            let skipped = matches!(end, Ok(End::Skipped));
            if opts.handoff {
                paint_handoff(&mut stdout, mode, opts.fade_to)?;
            }
            guard.restore(opts.handoff);
            if skipped {
                Outcome::Skipped
            } else {
                Outcome::Completed
            }
        }
        Ok(End::Interrupted(code)) => {
            guard.restore(false);
            Outcome::Interrupted(code)
        }
        Err(e) => {
            guard.restore(false);
            // the terminal hung up: the write fails (EIO) as SIGHUP arrives; report the signal
            if let sig @ 1.. = SIGNAL.load(Ordering::SeqCst) {
                return Ok(Report::from(Outcome::Interrupted(128 + sig)));
            }
            return Err(e);
        }
    };
    Ok(Report {
        outcome,
        seconds: start.elapsed().as_secs_f32(),
        frames,
        slow_from,
        flow_control: flow.answered_any,
        cell_aspect: opts.cell_aspect(),
        level_frames: level_frames.to_vec(),
        quality: worst_level,
        min_fps: min_fps as u32,
        quality_from,
        slow_until,
        cell_aspect_measured: measured,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(bytes: &[u8]) -> (Input, Vec<u8>) {
        let mut buf = bytes.to_vec();
        let got = parse_input(&mut buf);
        (got, buf)
    }

    #[test]
    fn answers_are_not_keys() {
        let (got, rest) = parse(b"\x1b[?62;22c\x1b[?1;2c");
        assert_eq!(got, Input { answers: 2, key: false, ctrl_c: false });
        assert!(rest.is_empty());
    }

    #[test]
    fn unfinished_answers_wait_for_the_rest() {
        for part in [&b"\x1b"[..], b"\x1b[", b"\x1b[?", b"\x1b[?62;2"] {
            let mut input = b"\x1b[?6c".to_vec();
            input.extend_from_slice(part);
            let (got, rest) = parse(&input);
            assert_eq!(got, Input { answers: 1, key: false, ctrl_c: false }, "{part:?}");
            assert_eq!(rest, part);
        }
    }

    #[test]
    fn keys_and_ctrl_c() {
        assert_eq!(parse(b"q").0, Input { answers: 0, key: true, ctrl_c: false });
        assert_eq!(parse(b"\x1b[A").0, Input { answers: 0, key: true, ctrl_c: false });
        let (got, rest) = parse(b"\x1b[?62c\x03\x1b[?62c");
        assert_eq!(got, Input { answers: 2, key: true, ctrl_c: true });
        assert!(rest.is_empty());
        // Esc followed by a letter (Alt+x) is a key; the answer after it still counts
        let (got, _) = parse(b"\x1bx\x1b[?1c");
        assert_eq!((got.answers, got.key), (1, true));
        // an answer to some other query is a key too (nothing else is asked)
        assert!(parse(b"\x1b[?62;1R").0.key);
    }

    #[test]
    fn window_holds_a_round_trip() {
        let mut flow = Flow::new();
        assert_eq!(flow.window(60.0), MIN_IN_FLIGHT, "before the first answer");
        for (ms, want) in [(1, MIN_IN_FLIGHT), (60, 4 + MARGIN), (150, 9 + MARGIN), (2000, MAX_IN_FLIGHT)] {
            flow.min_rtt = Some(Duration::from_millis(ms));
            assert_eq!(flow.window(60.0), want, "{ms} ms");
        }
        // a 60 ms link at 60 fps: a round trip's frames plus the margin can be on their way
        flow.min_rtt = Some(Duration::from_millis(60));
        assert!(flow.window(60.0) as f32 / 0.06 >= 60.0 * 1.5);
        assert_eq!(flow.window(30.0), 2 + MARGIN);
    }

    #[test]
    fn rate_needs_a_whole_second() {
        let mut flow = Flow::new();
        let t0 = flow.counting;
        for k in 0..30 {
            flow.processed(t0 + Duration::from_millis(k * 20), 1000);
        }
        assert_eq!(flow.rate(t0 + Duration::from_millis(600)), None);
        assert_eq!(flow.rate(t0 + Duration::from_millis(1000)), Some(30));
        assert_eq!(flow.rate(t0 + Duration::from_millis(1300)), Some(15));
    }

    #[test]
    fn throughput_counts_the_bytes_of_the_last_second() {
        let mut flow = Flow::new();
        let t0 = flow.counting;
        for k in 0..60 {
            flow.processed(t0 + Duration::from_millis(k * 20), 500);
        }
        assert_eq!(flow.throughput(t0 + Duration::from_millis(500)), None, "not a whole second yet");
        assert_eq!(flow.throughput(t0 + Duration::from_millis(1180)), Some(51 * 500));
    }

    #[test]
    fn quality_ladder_gives_up_bytes_in_order() {
        assert_eq!(QUALITY.len(), QUALITY_BYTES.len());
        assert_eq!(QUALITY[0], Quality { still_grain: false, tolerance: crate::intro::output::TOLERANCE, pairs: false, merge: false });
        assert!(QUALITY_BYTES.windows(2).all(|w| w[1] < w[0]), "each level is cheaper");
        assert!(QUALITY.iter().skip(1).all(|q| q.still_grain));
    }

    #[test]
    fn coarsening_halves_the_film_resolution() {
        use crate::intro::grade::Image;
        let mut img = Image::new(4, 4);
        for (i, p) in img.px.iter_mut().enumerate() {
            *p = crate::intro::math::V3::splat(i as f32 / 16.0);
        }
        let orig = img.clone();
        coarsen(&mut img, 2, QUALITY[4]);
        assert_eq!(img.px, orig.px, "tolerance levels leave the pixels alone");
        coarsen(&mut img, 2, QUALITY[5]);
        for r in 0..2 {
            for x in 0..4 {
                assert_eq!(img.get(x, 2 * r), img.get(x, 2 * r + 1), "a cell is one colour");
            }
        }
        let mut img = orig.clone();
        coarsen(&mut img, 2, QUALITY[6]);
        for y in 0..4 {
            assert_eq!(img.get(0, y), img.get(1, y));
            assert_eq!(img.get(2, y), img.get(3, y), "horizontal neighbours share a colour");
        }
        let mean = |i: &Image| i.px.iter().fold(0.0, |a, p| a + p.x) / i.px.len() as f32;
        assert!((mean(&img) - mean(&orig)).abs() < 1e-5, "the average colour is kept");
    }

    #[test]
    fn resolution_is_the_last_thing_given_up() {
        // tiers: never more frames further down, never a coarser level allowed earlier
        assert_eq!(TIERS[0].0, T::FPS);
        assert!(TIERS.windows(2).all(|w| w[1].0 <= w[0].0 && w[1].1 >= w[0].1));
        // every tier but the last keeps the film at full resolution
        let full_res = |max: usize| QUALITY[..=max].iter().all(|q| !q.pairs && !q.merge);
        for &(fps, max) in &TIERS[..TIERS.len() - 1] {
            assert!(full_res(max), "{fps} fps allows half resolution");
        }
        // ... and only the last allows half resolution, at the lowest frame rate
        let (fps, max) = TIERS[TIERS.len() - 1];
        assert!(!full_res(max) && fps == TIERS.iter().map(|t| t.0).fold(f32::MAX, f32::min));
        // the frame rates divide the display's 60 Hz evenly
        assert!(TIERS.iter().all(|t| (60.0 / t.0).fract() == 0.0));
    }
}
