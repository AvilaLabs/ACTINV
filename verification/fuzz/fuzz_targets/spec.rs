#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if data.len() > 65_536 {
        return;
    }
    if let Ok(text) = std::str::from_utf8(data) {
        let _ = actinv_core::spec::Spec::from_json(text);
    }
});
