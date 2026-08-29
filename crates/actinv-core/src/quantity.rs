//! Zero-cost physical quantities used at ACTINV's spec/core and reaction-rate boundaries.
//!
//! The JSON wire format deliberately remains ordinary numbers. These private-field wrappers make
//! dimensions distinct after validation without changing their binary64 representation.
//!
//! ```compile_fail
//! use actinv_core::quantity::{ElectronVolts, Seconds};
//!
//! fn advance(_duration: Seconds) {}
//! advance(ElectronVolts::new(300.0).unwrap());
//! ```

use std::fmt;
use std::ops::{Add, AddAssign, Mul};

const BARN_IN_SQUARE_CENTIMETRES: f64 = 1.0e-24;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QuantityError {
    quantity: &'static str,
    requirement: &'static str,
}

impl QuantityError {
    const fn new(quantity: &'static str, requirement: &'static str) -> Self {
        Self {
            quantity,
            requirement,
        }
    }
}

impl fmt::Display for QuantityError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{} must be {}", self.quantity, self.requirement)
    }
}

impl std::error::Error for QuantityError {}

fn finite_nonnegative(quantity: &'static str, value: f64) -> Result<f64, QuantityError> {
    if value.is_finite() && value >= 0.0 {
        Ok(value)
    } else {
        Err(QuantityError::new(quantity, "finite and nonnegative"))
    }
}

fn finite_positive(quantity: &'static str, value: f64) -> Result<f64, QuantityError> {
    if value.is_finite() && value > 0.0 {
        Ok(value)
    } else {
        Err(QuantityError::new(quantity, "finite and positive"))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct Seconds(f64);

impl Seconds {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_nonnegative("seconds", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }
}

impl Add for Seconds {
    type Output = Self;

    fn add(self, right: Self) -> Self::Output {
        Self(self.0 + right.0)
    }
}

impl AddAssign for Seconds {
    fn add_assign(&mut self, right: Self) {
        self.0 += right.0;
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct ElectronVolts(f64);

impl ElectronVolts {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_nonnegative("electronvolts", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct Kelvin(f64);

impl Kelvin {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_nonnegative("kelvin", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct Grams(f64);

impl Grams {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_positive("grams", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct AtomsPerGram(f64);

impl AtomsPerGram {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_nonnegative("atoms per gram", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct ParticleFlux(f64);

impl ParticleFlux {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_nonnegative("particle flux", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }

    pub(crate) fn sum_groups(values: &[f64]) -> Self {
        Self(values.iter().sum())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct FluxMultiplier(f64);

impl FluxMultiplier {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_nonnegative("flux multiplier", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }
}

impl Mul<FluxMultiplier> for ParticleFlux {
    type Output = Self;

    fn mul(self, right: FluxMultiplier) -> Self::Output {
        Self(self.0 * right.0)
    }
}

impl Mul<FluxMultiplier> for Seconds {
    type Output = Self;

    fn mul(self, right: FluxMultiplier) -> Self::Output {
        Self(self.0 * right.0)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct ParticleFluence(f64);

impl ParticleFluence {
    pub const fn get(self) -> f64 {
        self.0
    }
}

impl Mul<Seconds> for ParticleFlux {
    type Output = ParticleFluence;

    fn mul(self, right: Seconds) -> Self::Output {
        ParticleFluence(self.0 * right.0)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct CrossSectionBarns(f64);

impl CrossSectionBarns {
    pub fn new(value: f64) -> Result<Self, QuantityError> {
        finite_nonnegative("cross section in barns", value).map(Self)
    }

    pub const fn get(self) -> f64 {
        self.0
    }

    pub(crate) fn from_collapsed_kernel(value: f64) -> Self {
        debug_assert!(value.is_finite() && value >= 0.0);
        Self(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct RatePerBarnSecond(f64);

impl RatePerBarnSecond {
    pub fn from_particle_flux(flux: ParticleFlux) -> Self {
        Self(BARN_IN_SQUARE_CENTIMETRES * flux.0)
    }

    pub const fn get(self) -> f64 {
        self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct RatePerSecond(f64);

impl RatePerSecond {
    pub const fn get(self) -> f64 {
        self.0
    }
}

impl Mul<RatePerBarnSecond> for CrossSectionBarns {
    type Output = RatePerSecond;

    fn mul(self, right: RatePerBarnSecond) -> Self::Output {
        RatePerSecond(self.0 * right.0)
    }
}

#[derive(Debug, Clone)]
pub(crate) struct GroupFluxes(Vec<f64>);

impl GroupFluxes {
    pub(crate) fn new(values: Vec<f64>) -> Result<Self, QuantityError> {
        if values
            .iter()
            .all(|value| value.is_finite() && *value >= 0.0)
        {
            Ok(Self(values))
        } else {
            Err(QuantityError::new("group fluxes", "finite and nonnegative"))
        }
    }

    pub(crate) fn values(&self) -> &[f64] {
        &self.0
    }

    pub(crate) fn total(&self) -> ParticleFlux {
        ParticleFlux::sum_groups(&self.0)
    }
}

#[derive(Debug, Clone)]
pub(crate) struct EnergyBoundaries(Vec<f64>);

impl EnergyBoundaries {
    pub(crate) fn new(values: Vec<f64>) -> Result<Self, QuantityError> {
        if values.len() >= 2
            && values
                .iter()
                .all(|value| value.is_finite() && *value >= 0.0)
            && values.windows(2).all(|window| window[1] > window[0])
        {
            Ok(Self(values))
        } else {
            Err(QuantityError::new(
                "energy boundaries",
                "finite, nonnegative and strictly ascending",
            ))
        }
    }

    pub(crate) fn values(&self) -> &[f64] {
        &self.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::{align_of, size_of};

    #[test]
    fn scalar_quantities_have_exactly_f64_layout() {
        macro_rules! same_layout {
            ($($quantity:ty),+ $(,)?) => {
                $(
                    assert_eq!(size_of::<$quantity>(), size_of::<f64>());
                    assert_eq!(align_of::<$quantity>(), align_of::<f64>());
                )+
            };
        }
        same_layout!(
            Seconds,
            ElectronVolts,
            Kelvin,
            Grams,
            AtomsPerGram,
            ParticleFlux,
            FluxMultiplier,
            ParticleFluence,
            CrossSectionBarns,
            RatePerBarnSecond,
            RatePerSecond,
        );
    }

    #[test]
    fn physical_operations_preserve_expected_binary64_arithmetic() {
        let flux = ParticleFlux::new(2.5e14).unwrap();
        let multiplier = FluxMultiplier::new(4.0).unwrap();
        let duration = Seconds::new(3.0).unwrap();
        assert_eq!(((flux * multiplier) * duration).get(), 3.0e15);

        let cross_section = CrossSectionBarns::new(2.0).unwrap();
        let rate_per_barn = RatePerBarnSecond::from_particle_flux(flux);
        assert_eq!(
            (cross_section * rate_per_barn).get().to_bits(),
            (cross_section.get() * rate_per_barn.get()).to_bits()
        );
    }

    #[test]
    fn constructors_reject_wrong_sign_and_nonfinite_values() {
        assert!(Seconds::new(-1.0).is_err());
        assert!(Grams::new(0.0).is_err());
        assert!(Kelvin::new(f64::NAN).is_err());
        assert!(ParticleFlux::new(f64::INFINITY).is_err());
        assert!(CrossSectionBarns::new(-f64::EPSILON).is_err());
    }
}
