//! The intro's classic procedural motion, from the earlier real-time version (see
//! `archive/sdf-renderer/`): a footstep walk with planted feet, a three-step turn, a sit-down in
//! beats with hands on the armrests, then getting comfortable. `main.rs` exports it per frame.
pub mod camera;
pub mod gait;
pub mod math;
pub mod poses;
pub mod scene;
pub mod skeleton;
pub mod sweep;
pub mod timeline;
pub mod timing;
