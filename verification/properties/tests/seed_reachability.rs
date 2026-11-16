//! Ensure the fuzz seeds reach valid production-reader paths.
#[test]
fn independent_synthetic_npz_seed_reaches_valid_library() {
    let bytes = include_bytes!("../../fuzz/seeds/npz/small-library.npz");
    let library = actinv_data::library::read_npz_bytes(bytes).unwrap();
    assert_eq!(library.rows.len(), 1);
    assert_eq!(library.ngroups, 2);
    assert_eq!(library.sig, vec![1.0, 2.0]);
    assert_eq!(library.bounds, vec![1.0, 2.0, 3.0]);
}

#[test]
fn example_json_seed_reaches_a_valid_specification() {
    actinv_core::spec::Spec::from_json(include_str!("../../../examples/fns_fe_5min.json")).unwrap();
}
