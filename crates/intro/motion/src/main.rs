//! Prints the classic motion as JSON lines, one per frame at 60 fps (0..=526): joint positions,
//! body/trunk/chest/head frames (columns: forward, up, right) and the camera. Old-scene axes:
//! Y up, the chair at (-1.2, 0, 0) facing +X. `blender/scene.py` maps them onto its scene.
use force_intro_motion::math::{M3, V3};
use force_intro_motion::timeline::{frame_at, Style};
use force_intro_motion::timing as T;

fn v(p: V3) -> String {
    format!("[{:.5},{:.5},{:.5}]", p.x, p.y, p.z)
}

fn m(r: M3) -> String {
    format!("[{},{},{}]", v(r.col(0)), v(r.col(1)), v(r.col(2)))
}

fn main() {
    let n = T::total_frames();
    for f in 0..=n {
        let t = f as f32 / T::FPS;
        let fs = frame_at(t, Style { life: true });
        let j = &fs.joints;
        let pair = |a: [V3; 2]| format!("[{},{}]", v(a[0]), v(a[1]));
        println!(
            "{{\"t\":{t:.4},\"pelvis\":{},\"spine_mid\":{},\"neck\":{},\"neck_top\":{},\"head\":{},\
             \"shoulder\":{},\"elbow\":{},\"hand\":{},\"hip\":{},\"knee\":{},\"ankle\":{},\"heel\":{},\"toe\":{},\
             \"body\":{},\"trunk\":{},\"chest\":{},\"head_frame\":{},\
             \"cam_pos\":{},\"cam_target\":{},\"cam_fov\":{:.4}}}",
            v(j.pelvis), v(j.spine_mid), v(j.neck), v(j.neck_top), v(j.head),
            pair(j.shoulder), pair(j.elbow), pair(j.hand), pair(j.hip), pair(j.knee), pair(j.ankle),
            pair(j.heel), pair(j.toe),
            m(j.body), m(j.trunk), m(j.chest), m(j.head_frame),
            v(fs.camera.pos), v(fs.camera.target), fs.camera.fov,
        );
    }
}
