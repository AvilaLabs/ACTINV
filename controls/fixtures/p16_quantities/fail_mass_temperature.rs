use actinv_core::quantity::{Grams, Kelvin};

fn set_mass(_mass: Grams) {}

fn main() {
    set_mass(Kelvin::new(293.6).unwrap());
}
