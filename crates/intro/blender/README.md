# Rendering the film

Needs Blender 4.5 LTS (the portable Linux build, e.g. at
`~/.local/opt/blender-4.5.14-linux-x64/blender`; fetch it with `curl -LO https://download.blender.org/release/Blender4.5/blender-4.5.14-linux-x64.tar.xz`, check sha256 `9ba871ff2ecd36526b77432745980b7e6664ecd0c7ca11c48849073dcfe06da3`, unpack into `~/.local/opt/`) and the assets from
[../ASSETS.md](../ASSETS.md) under `$FORCE_INTRO_ASSETS`.

```sh
B=~/.local/opt/blender-4.5.14-linux-x64/blender
OUT=~/force-intro-assets/render/v25
cargo build --release                                              # the player draws the screen's splash texture
nice -n 10 $B -b --python blender/scene.py -- --out $OUT          # 527 frames at 60 fps, ~7 min
nice -n 10 $B -b --python blender/pack.py -- $OUT assets/intro.film
cargo build --release
```

After a change to the splash only (`src/intro/splash.rs`), re-render just the frames where the screen
is lit: copy the previous render to a new directory and run `scene.py` with `--frames 310-526` into it
(~6 min with the scene build). Before 5.3 s the screen's emission is off, so the frames before are unchanged (frames
310–318 come out identical, which checks it).

| `scene.py` option | Meaning |
|---|---|
| `--out DIR` | render frames `0000.png` … `0526.png` into DIR |
| `--frames A-B` | only these frames |
| `--res WxH` | default `320x200` (the film's size, 16:10; even). The shots are composed for 2.39:1 (`DESIGN_ASPECT`): the camera keeps that horizontal view at any height |
| `--samples N` | Cycles samples, default 64 (denoised) |
| `--jobs N` | render in N parallel Blender processes (default 8; 1 = in this process). The scene is built once (~4.5 min, single-threaded) and saved; each job renders every Nth frame from it with its share of the threads. Output is identical to a single process |
| `--save FILE.blend` | also save the built scene, to open in Blender's UI |

`FORCE_INTRO_THREADS` sets the render threads in all (default: every core; with `--jobs 1`, 48).
Measured on a 128-thread server: one process 18.5 min of rendering, 8 jobs × 16 threads 3.3 min.
On the machine measured, its GPUs (mining-class cards) rendered a frame slower than the CPU (1.9 s vs 1.45 s, plus
an 8 min first-time kernel compile), so the scripts render on the CPU.

## Validate the motion

```sh
nice -n 10 $B -b --python blender/scene.py -- --save /tmp/intro.blend
nice -n 10 $B -b /tmp/intro.blend --python blender/validate.py -- --out /tmp/check --sheets
```

`report.md` lists foot skate, feet in the floor, jolts (pelvis, head, hands), the laptop against the body, suit and chair (posed vertices inside its base or lid), the camera too close to anything, the screen hidden at the end, legs or arms through each
other (capsules around the bones), the body through the chair, balance while standing, knees bending
backward, and where he ends up on the seat. `--sheets` renders the figure from the front, side and
above every 0.1 s per shot (walk, turn, sit, laptop, settle), and close-ups of the laptop and hands every 0.25 s from three sides, under `frames/`, for looking at. Run it after
every motion change. On the current film the flags are: a few frames per foot where a heel lands while the foot is still moving slightly (part of the original motion); the calves brushing the seat's front edge for a few frames as he turns; the camera passing 4 cm from his hair just before the end (it looks away from him, at the screen); and a forearm touching the laptop by about 1 cm for 2–3 frames while he opens it.

## How the scene is built

| Step | What happens |
|---|---|
| character | Quaternius base male; `SLIM` pulls arm, shoulder and chest mass toward the bones for an athletic, lean build |
| suit | a copy of the body: head and hands removed, muscle definition smoothed out, inflated (jacket more than trousers, so the hem shows). The shirt V, tie, lapels, collar, cuffs, hem, button and shoes are drawn by one shader from rest-pose positions, so the edges stay crisp |
| motion | the classic procedural motion (footstep walk, three-step turn, sit-down in beats with hands on the armrests, leaning back against the backrest, head forward, looking at the laptop), exported per frame by `motion/` (`cargo run --release --manifest-path motion/Cargo.toml > motion/classic.jsonl`) and mapped onto the rig: the torso follows the exported trunk, chest and head frames; legs and arms reach the exported ankles and wrists by IK, bending toward the exported knees and elbows; feet and toes are aimed so a flat foot stays flat; seated, the body is lifted onto the cushion (`SEAT_LIFT`) |
| laptop | Poly Haven classic laptop, the lid on its own hinge. Carried closed in the left hand at his side (swinging slightly); brought round in front of him during the turn, held flat by its near-left corner (so it stays clear of the armrest and his body while he sits); put down on his thighs as he lands; opened by the right hand at the lid's top edge, the lid stopping where the screen faces his eyes; then both hands on the keyboard, relaxed: wrists and fingers from the library's seated idle, fingers curled part way toward its steering-wheel grip (`FINGER_CURL`), elbows sinking to his sides. On his lap it rests on top of the posed suit's thighs, measured each frame. Overrides the arms' IK targets. The screen lights up as the lid opens and shows the brand splash at rest: `light_screen()` has the player draw it (`force-intro --splash-texture`) laid out so that the part the final camera sees is exactly the player's splash picture (it projects the display's corners into the final camera), so the film crossfades into the same composition drawn by the terminal. Cycles' tone mapping (AgX, its look, exposure) would change its colours, so the texture is pre-compensated with a measured inverse (`tone_inverse()`: a 17³ grid of emission colours rendered through the same view settings, cached in `$FORCE_INTRO_ASSETS/splash/tone_inverse.npy`; delete it after changing the look) |
| camera | the classic camera path from the same export, until `CAMERA_TAKEOVER`; then one continuous move on a single eased curve: it spirals round the screen from the front past his left side and behind him (rising `CAMERA_RISE` over the chair's back) to just in front of his eyes (`CAMERA_END_OFFSET`), the look-at point moving onto the screen and the field of view narrowing (mostly at the end) until the display's width fills the frame at `SCREEN_FILL_AT`; it holds there while the player fades out |
| chair | Poly Haven mid-century lounge chair, reshaped by `CHAIR_SHAPE` (12% wider, 8% shallower, 15% lower: at its own size his knees could not clear its front edge and his hips did not fit the seat). The leather gives where he presses on it: a geometry-nodes modifier moves every chair point inside his posed suit out to just under the suit's surface (`soften_chair()`, `CUSHION_GIVE`). `CHAIR` can point at `modern_arm_chair_01` instead (recoloured burgundy) |
| look | Cycles, AgX, key/rim/fill area lights; fades, vignette and grain are added at playback, which crops the film to the terminal's shape |
