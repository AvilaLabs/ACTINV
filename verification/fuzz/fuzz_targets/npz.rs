#![no_main]
use libfuzzer_sys::fuzz_target;
use std::io::{Cursor, Read, Write};

const SEED: &[u8] = include_bytes!("../seeds/npz/small-library.npz");

fn structured_member(data: &[u8], selected: usize) -> Vec<u8> {
    let mut seed = zip::ZipArchive::new(Cursor::new(SEED)).expect("fixed synthetic seed");
    let mut output = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (index, name) in ["rows.npy", "sig.npy", "bounds.npy"]
        .into_iter()
        .enumerate()
    {
        output.start_file(name, options).unwrap();
        if index + 1 == selected {
            output.write_all(data).unwrap();
        } else {
            let mut member = Vec::new();
            seed.by_name(name)
                .unwrap()
                .read_to_end(&mut member)
                .unwrap();
            output.write_all(&member).unwrap();
        }
    }
    output.finish().unwrap().into_inner()
}

fuzz_target!(|data: &[u8]| {
    if data.len() > 65_536 {
        return;
    }
    let selected = usize::from(data.first().copied().unwrap_or(0) % 4);
    if selected != 0 {
        // Repair the ZIP envelope and CRC while mutating one NPY member, so
        // checksum rejection does not block the array-header/shape decoder.
        let wrapped = structured_member(&data[1..], selected);
        let _ = actinv_data::library::read_npz_bytes(&wrapped);
        return;
    }
    // Keep this search focused on small archives, not decompression bombs.
    // Oversized declarations remain covered by dedicated production regressions.
    let Ok(mut archive) = zip::ZipArchive::new(Cursor::new(data)) else {
        return;
    };
    if archive.len() > 32 {
        return;
    }
    let mut decoded = 0u64;
    for index in 0..archive.len() {
        let Ok(member) = archive.by_index(index) else {
            return;
        };
        decoded = decoded.saturating_add(member.size());
        if decoded > 1_048_576 {
            return;
        }
    }
    let _ = actinv_data::library::read_npz_bytes(data);
});
