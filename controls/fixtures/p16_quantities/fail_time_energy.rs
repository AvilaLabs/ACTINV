use actinv_core::quantity::{ElectronVolts, Seconds};

fn advance(_duration: Seconds) {}

fn main() {
    advance(ElectronVolts::new(300.0).unwrap());
}
