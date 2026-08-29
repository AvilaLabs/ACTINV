use actinv_core::quantity::{CrossSectionBarns, Seconds};

fn main() {
    let cross_section = CrossSectionBarns::new(2.0).unwrap();
    let duration = Seconds::new(3.0).unwrap();
    let _rate = cross_section * duration;
}
