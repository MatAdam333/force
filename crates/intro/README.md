# force-intro

A ~12 s cinematic startup animation for Force. A man in a dark suit carries a laptop to a lounge chair,
turns and sits down while the camera orbits from the side to the front, opens the laptop and settles back
against the backrest, working on it. In one continuous move the camera spirals round his left side
and behind him to his eye line, zooming in until the laptop's screen fills the frame. The screen shows
the Force brand splash ("Underline, black") from the moment the lid opens: on black, the italic
`FORCE` wordmark (Archivo Black, coral to peach), a thin underline and the name,
`The code cockpit`. There the film crossfades into the same composition drawn by the terminal
itself (half-block pixels, real text), which comes alive: speed streaks rush into the word and fade,
a light sweeps over the word, a soft light runs along the underline and a working shimmer passes
over the name; then it fades into the app. After the crossfade the splash is entirely
terminal-native (the encoder switches to exact output then). A grey `skip intro · any key` sits
over the picture's bottom-right corner throughout: text cells on their own black background (a
chip), drawn over the film without touching its pixels.

It's a film: rendered offline in Blender from CC0 assets (a rigged human, a Poly Haven lounge chair and laptop;
see [ASSETS.md](ASSETS.md)) moved by the intro's own procedural motion (`motion/`), embedded in the binary (`assets/intro.film`, 6.0 MB), and
played in the terminal at 60 fps, filling it: the shots are composed for 2.39:1 but rendered at
16:10 with the same horizontal view, and the player crops whatever overhangs the terminal (a 16:9
full screen loses a little off the top and bottom, a narrow window a little off the sides). The cell
shape is measured from the terminal's size in pixels, so the picture isn't stretched. Playback costs about 2 ms per frame on one core.

Playback is real time on any terminal, local or over ssh: each frame is followed by a device
attributes query (`ESC [ c`), which a terminal answers only once it has processed the frame. The
frames allowed unanswered are what one round trip holds at the frame rate (the shortest round trip
seen) plus two, so a link's latency costs no frames (60 ms over a VPN: 6 in flight, full 60 fps);
frames that fall due while the terminal is further behind are dropped, so the picture is at most a
round trip and a few frames late and the intro always ends on time. A terminal or link that can't
keep up gives up, in order: bytes per frame at full resolution, then frames, and resolution last (a
pixelated film is more noticeable than fewer frames). Rate control measures what it carries per
second while it is the bottleneck and encodes each frame at the finest quality level that fits that
budget (`term::QUALITY`: still grain, then a coarser tolerance for skipping and pen reuse); when the
coarsest of those hasn't kept up for a second it steps to 30, then 20 fps (`term::TIERS`), and only
at 20 fps may the film go to half resolution. It steps back up when frames fit with room to spare,
and the native splash (always exact) gets 60 fps again. A terminal that never answers (within 1 s)
is played without flow control (its blocking writes drive the same rate control). Before exiting it waits for the outstanding
answers, so none reaches the next program as input. Only cells that changed are written, and colours
are chosen to reuse the pen (within an invisible tolerance), see `src/intro/output.rs`.

A Rust crate (binary `force-intro`) in Force's `crates/intro/`; Force builds it with the rest (`deploy.sh`,
`scripts/build-release.sh`). Its command-line interface is described in SPEC §31.

## Run and validate

```sh
cargo build --release
./target/release/force-intro                    # play it (any key skips, Ctrl+C aborts)
./target/release/force-intro --png sheet.png --at 2.8,4.6,7.0,9.5,10.6,11.2 --size 120x40   # contact sheet
./target/release/force-intro --ansi 9.0 --size 100x30 --color ascii                # one frame as text
./target/release/force-intro --bench --size 120x40   # ms per frame, output KB per frame
tools/preview.sh intro.mp4                          # the whole intro as a terminal shows it (ffmpeg)
./target/release/force-intro --quality 4 --verbose   # hold one quality level (to look at it)
cargo test --release && cargo clippy --release --all-targets -- -D warnings
```

| Option | Meaning |
|---|---|
| `--animations full\|subtle\|off` | as in force: `subtle` = static grain; `off` = don't play |
| `--color auto\|truecolor\|256\|ascii` | `auto`: `COLORTERM`/`TERM`/locale (`NO_COLOR` → ascii) |
| `--fade-to default\|RRGGBB` | what the last frame fades into (`default` = the terminal's own background) |
| `--handoff` | after a normal end or a skip, leave the alternate screen up and blank, for a program that takes over at once |
| `--cell-aspect 2.0` | cell height/width; by default measured from the terminal's size in pixels (else 2.0) |
| `--min-size 80x24` | below this it doesn't play |

It doesn't play when stdin or stdout isn't a terminal, the terminal is smaller than 80x24, or
`--animations off` is set. Exit codes: 0 played, skipped or not shown; 130 Ctrl+C; 128+n signal n;
1 error; 2 usage. The terminal is restored on every path, including panics.

## Change the film

Motion and camera live in `motion/`; look and the character in `blender/scene.py`. How to render and pack is in
[blender/README.md](blender/README.md). In short: edit, render (about 5 minutes on a 128-thread machine), pack into
`assets/intro.film`, `cargo build`.

| What | Where |
|---|---|
| shot timing, poses, walk, turn and sit-down (the classic motion) | `motion/src/timing.rs`, `motion/src/poses.rs`, `motion/src/gait.rs`; re-export `motion/classic.jsonl` |
| camera path | `motion/src/camera.rs` |
| the chair's size and shape; how far he sinks into it; the seated lift onto the cushion | `CHAIR_SCALE`, `CHAIR_SHAPE`, `CUSHION_GIVE`, `SEAT_LIFT` in `blender/scene.py` |
| laptop: carry, onto the lap, lid opening, hands (beats in seconds) | `LAPTOP_BEATS`, `SIDE_LOCAL`, `PALM_LOCAL`, `animate_laptop()` |
| the splash: composition, colours, its build-in | `src/intro/splash.rs`; the wordmark mask: `tools/force/bake.py` (Pillow) -> `assets/force.mask`; check the composition against the design's reference renderer (vendored in `tools/force/reference/`) with `tools/force/compare.py`; re-render the film's lit-screen frames after changing it |
| when the splash takes over, the fade into the app | `SPLASH_IN`, `FADE_OUT` in `src/intro/timing.rs` |
| the camera's move round him and into the screen | `CAMERA_TAKEOVER`, `SCREEN_FILL_AT`, `CAMERA_END_OFFSET`, `CAMERA_RISE` |
| body build (slimmed from the base model) | `SLIM` |
| suit, shirt, tie, hair, leather colours | the palette at the top of `scene.py` |
| suit details (V opening, lapels, tie, hem, cuffs, collar) | `suit_material()` and the `HEM`/`COLLAR`/`CUFF` heights |
| lights, floor, rug | `lights_and_world()` |
| fades, vignette, grain (added at playback) | `src/intro/timing.rs`, `src/intro/grade.rs` |

## Layout

| File | Concern |
|---|---|
| `blender/scene.py` | builds the scene from the assets and renders the frames |
| `blender/pack.py` | packs rendered frames into the film file |
| `motion/` | the classic procedural motion (Rust), exported per frame to `motion/classic.jsonl` |
| `blender/validate.py` | motion checks (skate, floor, jolts, overlaps, chair, balance, joints) and review sheets |
| `assets/intro.film` | the film (320x200, 16:10, 60 fps, 527 frames: up to the screen filling the picture) |
| `src/intro/film.rs` | film format and decoding (YCoCg 4:2:0, frame-to-frame deltas, zlib) |
| `src/intro/grade.rs` | frame → terminal pixels: scaling to cover the terminal, fades, vignette, grain |
| `src/intro/splash.rs` | the brand splash: drawn on the terminal after the film, and the film's screen texture (`force-intro --splash-texture`) |
| `assets/force.mask`, `tools/force/` | the `FORCE` wordmark baked from Archivo Black (OFL 1.1), its bake and compare scripts |
| `src/intro/output.rs` | half-block truecolor and 256-colour, ASCII ramp; diffed output, pen reuse |
| `src/intro/term.rs` | raw mode and alternate screen, restore guard, signals, real-time loop with flow control, input, resize |
| `archive/` | earlier versions kept for reference, not built: the real-time ray-marcher, the motion-library and motion-capture variants |

Measured on a 128-thread x86-64 server (`--bench` prints every quality level; truecolor, 60 fps, the picture filling the
terminal): about 3 ms/frame to render, single-threaded. Output at full quality averages 19.6 KB per
frame at 120x40 (1.2 MB/s) and 34 KB at 191x47 (2.1 MB/s); the coarsest level needs 0.24 and
0.42 MB/s. On a link carrying 1 MB/s (simulated, 30 ms latency) 191x47 plays at 51 fps and 120x40 at
58 (v11: 25 and 32); v13 keeps those links at full resolution with fewer frames in the heaviest
shots instead of half resolution. The binary is 8.4 MB with the film.
