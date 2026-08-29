use actinv_core::run::PreparedRun;
use actinv_core::spec::{
    parse_duration, DecayRef, FissionYieldOptions, LibraryRef, PhotonOptions, Spec,
};

fn legacy_prepare(
    library: &LibraryRef,
    decay: &DecayRef,
    photon: &PhotonOptions,
    fission: &FissionYieldOptions,
    temperature_k: f64,
) -> Result<PreparedRun, String> {
    PreparedRun::prepare_inputs(
        library,
        decay,
        photon,
        fission,
        actinv_core::spec::Projectile::Neutron,
        temperature_k,
    )
}

fn main() {
    let mut spec = Spec::from_json(
        r#"{
          "spec":"actinv-spec-1",
          "library":{"path":"activation.npz"},
          "decay":{"primary":"decay.endf"},
          "material":{"mass_g":1.0,"basis":"wt_percent","composition":{"FE":100.0}},
          "spectrum":{"structure":"custom","boundaries_eV":[1.0,2.0],"flux_per_group":[1.0]},
          "schedule":[{"dt":"1 s","flux":1.0}]
        }"#,
    )
    .unwrap();
    let _: f64 = spec.material.mass_g;
    spec.material.mass_g = 2.0;
    let _: f64 = spec.options.temperature_K;
    let _: f64 = spec.options.bmin_atoms_per_g;
    let _: Vec<f64> = spec.flux_ascending();
    let _: Vec<(f64, f64)> = spec.schedule_seconds();
    let _: Vec<f64> = spec.photon_boundaries();
    let _: Result<f64, String> = parse_duration("5 min");
    let _signature = legacy_prepare;
}
