//! Wall-clock timing that also builds for the browser. `std::time::Instant::now` panics on
//! `wasm32-unknown-unknown`, which has no clock; there every elapsed time reads zero, and the
//! browser workbench times a run itself. Nothing computed depends on these readings.

#[cfg(not(target_arch = "wasm32"))]
pub use std::time::Instant;

#[cfg(target_arch = "wasm32")]
#[derive(Clone, Copy, Debug)]
pub struct Instant;

#[cfg(target_arch = "wasm32")]
impl Instant {
    pub fn now() -> Self {
        Instant
    }

    pub fn elapsed(&self) -> std::time::Duration {
        std::time::Duration::ZERO
    }
}
