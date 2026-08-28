//! Standalone ACTINV command-line entry point.

fn main() {
    actinv_cli::command::main_from(std::env::args().collect());
}
