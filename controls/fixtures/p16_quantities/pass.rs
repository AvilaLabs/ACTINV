use actinv_core::quantity::{
    AtomsPerGram, CrossSectionBarns, ElectronVolts, FluxMultiplier, Grams, Kelvin,
    ParticleFlux, RatePerBarnSecond, Seconds,
};

fn main() {
    let duration = Seconds::new(3.0).unwrap();
    let energy = ElectronVolts::new(14.1e6).unwrap();
    let temperature = Kelvin::new(293.6).unwrap();
    let mass = Grams::new(2.0).unwrap();
    let threshold = AtomsPerGram::new(1.0e-8).unwrap();
    let flux = ParticleFlux::new(2.5e14).unwrap();
    let multiplier = FluxMultiplier::new(4.0).unwrap();
    let cross_section = CrossSectionBarns::new(2.0).unwrap();
    let rate_per_barn = RatePerBarnSecond::from_particle_flux(flux);
    let rate = cross_section * rate_per_barn;
    let fluence = (flux * multiplier) * duration;

    assert_eq!(duration.get(), 3.0);
    assert_eq!(energy.get(), 14.1e6);
    assert_eq!(temperature.get(), 293.6);
    assert_eq!(mass.get(), 2.0);
    assert_eq!(threshold.get(), 1.0e-8);
    assert!(rate.get() > 0.0);
    assert_eq!(fluence.get(), 3.0e15);
    println!("p16-quantity-pass");
}
