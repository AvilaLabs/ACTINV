use actinv_core::quantity::{FluxMultiplier, ParticleFlux};

fn set_flux(_flux: ParticleFlux) {}

fn main() {
    set_flux(FluxMultiplier::new(2.0).unwrap());
}
