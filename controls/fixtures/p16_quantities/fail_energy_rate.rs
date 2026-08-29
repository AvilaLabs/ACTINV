use actinv_core::quantity::{ElectronVolts, RatePerSecond};

fn consume_rate(_rate: RatePerSecond) {}

fn main() {
    consume_rate(ElectronVolts::new(1.0e6).unwrap());
}
