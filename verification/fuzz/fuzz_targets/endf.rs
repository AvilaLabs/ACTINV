#![no_main]
use actinv_verification_kernels::endf;
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if data.len() > 65_536 {
        return;
    }
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    let _ = endf::parse_endf_float(text);
    let _ = endf::parse_endf_usize(text);
    let _ = endf::checked_fields(text);
    let _ = endf::checked_tail(text);
    let _ = endf::parse_sections(text);
    let lines: Vec<_> = text.lines().collect();
    let _ = endf::read_list_checked(&lines, 0);
    let _ = endf::read_tab1_checked(&lines, 0);
});
