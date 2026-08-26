//! P5-G1 helper: dump what the Rust readers see, for comparison against the Python implementations.
//!   dump decay FILE          -> "ZA LISO NST half_life e_light e_em e_heavy nmodes br0 q0 ..." per nuclide
//!   dump library FILE [PHI]  -> "target mt zap lfs lmf checksum" per row (checksum = sum of group values)
use actinv_data::{decay, library};
fn main() {
    let a: Vec<String> = std::env::args().collect();
    match a[1].as_str() {
        "decay" => {
            let m = decay::parse_file(&a[2]).expect("read decay file");
            let mut keys: Vec<_> = m.keys().copied().collect(); keys.sort();
            println!("{}", keys.len());
            for k in keys {
                let n = &m[&k];
                print!("{} {} {} {:.17e} {:.17e} {:.17e} {:.17e} {}", n.za, n.liso, n.nst, n.half_life, n.e_light(), n.e_em(), n.e_heavy(), n.modes.len());
                let mut ms: Vec<_> = n.modes.iter().map(|md| (md.rtyp, md.rfs, md.br, md.q)).collect();
                ms.sort_by(|x, y| x.partial_cmp(y).unwrap());
                for (rtyp, rfs, br, q) in ms { print!(" {:.17e} {:.17e} {:.17e} {:.17e}", rtyp, rfs, br, q); }
                println!();
            }
        }
        "library" => {
            // Write rows and sig as raw little-endian bytes so the control can test byte identity with numpy.
            // (A checksum cannot: floating-point addition is not associative, so summation order alone moves the last bit.)
            use std::io::Write;
            let l = library::read_npz(&a[2]).expect("read library");
            let out = &a[3];
            let mut fr = std::io::BufWriter::new(std::fs::File::create(format!("{out}.rows")).unwrap());
            for r in &l.rows { for v in [r.target as i64, r.mt as i64, r.zap as i64, r.lfs as i64, r.lmf as i64] { fr.write_all(&v.to_le_bytes()).unwrap(); } }
            fr.flush().unwrap();
            let mut fs = std::io::BufWriter::new(std::fs::File::create(format!("{out}.sig")).unwrap());
            for v in &l.sig { fs.write_all(&v.to_le_bytes()).unwrap(); }
            fs.flush().unwrap();
            println!("{} {}", l.rows.len(), l.ngroups);
        }
        _ => eprintln!("usage: dump decay|library FILE"),
    }
}
