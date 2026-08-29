use actinv_core::quantity::{AtomsPerGram, Grams};

fn set_threshold(_threshold: AtomsPerGram) {}

fn main() {
    set_threshold(Grams::new(1.0).unwrap());
}
