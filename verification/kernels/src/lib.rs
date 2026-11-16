//! Compile the production source files directly for lightweight verification.
//! No algorithm is copied or substituted here.
#![forbid(unsafe_code)]
// Kani's pinned nightly provides safe ASCII-to-str views for proof inputs.
#![cfg_attr(kani, feature(ascii_char))]

#[path = "../../../crates/actinv-data/src/endf.rs"]
#[allow(dead_code)] // Production-only crate-private consumers are outside this kernel crate.
pub mod endf;
#[doc(hidden)]
pub mod endf_fixtures;
#[path = "../../../crates/actinv-core/src/quantity.rs"]
#[allow(dead_code)] // Production-only crate-private consumers are outside this kernel crate.
pub mod quantity;
#[path = "../../../crates/actinv-core/src/sparse.rs"]
pub mod sparse;
#[path = "../../../crates/actinv-gui/src/worker_wait.rs"]
pub mod worker_wait;

#[cfg(kani)]
mod proofs;
