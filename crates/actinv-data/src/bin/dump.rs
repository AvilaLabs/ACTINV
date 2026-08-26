//! P5-G1 helper: dump what the Rust readers see, for comparison against the Python implementations.
//!   dump decay FILE          -> "ZA LISO NST half_life e_light e_em e_heavy nmodes br0 q0 ..." per nuclide
//!   dump spectra FILE [ZA:LISO ...] -> lossless line-oriented MF=8/MT=457 spectrum records
//!   dump spectra-summary FILE -> all-file section/spectrum and STYP/LCON counts
//!   dump library FILE OUT    -> raw row and group arrays for byte comparison
use actinv_data::{composition, decay, library};
fn main() {
    let a: Vec<String> = std::env::args().collect();
    match a[1].as_str() {
        "decay" => {
            let m = decay::parse_file(&a[2]).expect("read decay file");
            let mut keys: Vec<_> = m.keys().copied().collect();
            keys.sort();
            println!("{}", keys.len());
            for k in keys {
                let n = &m[&k];
                print!(
                    "{} {} {} {:.17e} {:.17e} {:.17e} {:.17e} {}",
                    n.za,
                    n.liso,
                    n.nst,
                    n.half_life,
                    n.e_light(),
                    n.e_em(),
                    n.e_heavy(),
                    n.modes.len()
                );
                let mut ms: Vec<_> = n
                    .modes
                    .iter()
                    .map(|md| (md.rtyp, md.rfs, md.br, md.q))
                    .collect();
                ms.sort_by(|x, y| x.partial_cmp(y).unwrap());
                for (rtyp, rfs, br, q) in ms {
                    print!(" {:.17e} {:.17e} {:.17e} {:.17e}", rtyp, rfs, br, q);
                }
                println!();
            }
        }
        "spectra" => {
            let m = decay::parse_file(&a[2]).expect("read decay file");
            let mut keys: Vec<_> = m.keys().copied().collect();
            keys.sort();
            if a.len() > 3 {
                let selected: std::collections::HashSet<(i32, i32)> = a[3..]
                    .iter()
                    .map(|value| {
                        let mut fields = value.split(':');
                        let za = fields.next().unwrap_or("").parse().expect("ZA in ZA:LISO");
                        let liso = fields
                            .next()
                            .unwrap_or("0")
                            .parse()
                            .expect("LISO in ZA:LISO");
                        (za, liso)
                    })
                    .collect();
                keys.retain(|key| selected.contains(key));
            }
            println!("{}", keys.len());
            for key in keys {
                let n = &m[&key];
                println!("N {} {} {}", n.za, n.liso, n.spectra.len());
                for (si, s) in n.spectra.iter().enumerate() {
                    println!("S {si} {:.17e} {} {} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {} {}",
                        s.styp, s.lcon, s.lcov, s.fd, s.d_fd, s.average_energy, s.d_average_energy,
                        s.fc, s.d_fc, s.discrete.len(), usize::from(s.continuous.is_some()));
                    for d in &s.discrete {
                        println!("D {si} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e}",
                            d.energy, d.d_energy, d.rtyp, d.transition_type, d.intensity, d.d_intensity,
                            d.pair_intensity, d.d_pair_intensity, d.conversion_total, d.d_conversion_total,
                            d.conversion_k, d.d_conversion_k, d.conversion_l, d.d_conversion_l);
                    }
                    if let Some(c) = &s.continuous {
                        println!(
                            "C {si} {:.17e} {} {}",
                            c.rtyp,
                            c.interpolation.len(),
                            c.points.len()
                        );
                        for (nbt, int) in &c.interpolation {
                            println!("R {si} {nbt} {int}");
                        }
                        for (e, p) in &c.points {
                            println!("P {si} {:.17e} {:.17e}", e, p);
                        }
                    }
                }
            }
        }
        "spectra-summary" => {
            let m = decay::parse_file(&a[2]).expect("read decay file");
            let mut counts: std::collections::BTreeMap<(i32, i32), usize> =
                std::collections::BTreeMap::new();
            let mut spectra = 0usize;
            for nuclide in m.values() {
                spectra += nuclide.spectra.len();
                for spectrum in &nuclide.spectra {
                    *counts
                        .entry((spectrum.styp.round() as i32, spectrum.lcon))
                        .or_default() += 1;
                }
            }
            println!("{} {}", m.len(), spectra);
            for ((styp, lcon), count) in counts {
                println!("C {styp} {lcon} {count}");
            }
        }
        "library" => {
            // Write rows and sig as raw little-endian bytes so the control can test byte identity with numpy.
            // (A checksum cannot: floating-point addition is not associative, so summation order alone moves the last bit.)
            use std::io::Write;
            let l = library::read_npz(&a[2]).expect("read library");
            let out = &a[3];
            let mut fr =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.rows")).unwrap());
            for r in &l.rows {
                for v in [
                    r.target as i64,
                    r.mt as i64,
                    r.zap as i64,
                    r.lfs as i64,
                    r.lmf as i64,
                ] {
                    fr.write_all(&v.to_le_bytes()).unwrap();
                }
            }
            fr.flush().unwrap();
            let mut fs =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.sig")).unwrap());
            for v in &l.sig {
                fs.write_all(&v.to_le_bytes()).unwrap();
            }
            fs.flush().unwrap();
            println!("{} {}", l.rows.len(), l.ngroups);
        }
        "composition" => {
            // dump composition '{"Fe":63.72,"Cr":18.28}' -> "ZA LISO atoms_per_g" per isotope, then the diagnostics
            let spec = &a[2];
            let mut el = std::collections::BTreeMap::new();
            for part in spec.trim_matches(|c| c == '{' || c == '}').split(',') {
                let mut kv = part.splitn(2, ':');
                let k = kv.next().unwrap_or("").trim().trim_matches('"').to_string();
                let v: f64 = kv.next().unwrap_or("0").trim().parse().unwrap_or(0.0);
                if !k.is_empty() {
                    el.insert(k, v);
                }
            }
            let (inv, diag) = composition::atoms_per_gram(&el);
            println!("{}", inv.len());
            for ((za, liso), n) in &inv {
                println!("{} {} {:.17e}", za, liso, n);
            }
            for (e, (molar, apg, niso)) in &diag.elements {
                println!("# {} {:.17e} {:.17e} {}", e, molar, apg, niso);
            }
            for u in &diag.unknown {
                println!("# UNKNOWN {}", u);
            }
        }
        "provenance" => println!("{}", composition::provenance()),
        _ => eprintln!(
            "usage: dump decay|spectra|spectra-summary|library|composition|provenance ..."
        ),
    }
}
