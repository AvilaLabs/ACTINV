//! Standalone ACTINV command-line entry point.
#![forbid(unsafe_code)]

fn main() {
    actinv_cli::command::main_from(std::env::args().collect());
}
