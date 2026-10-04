//! P20 G3 control probe: emit the collapsed MF=33 covariance for an explicit
//! row selection, so the deterministic sampling control can eigendecompose it
//! independently. Reads the pinned activation library, covariance sidecar,
//! group flux, and a comma-separated row list; prints the CollapsedCovariance
//! as JSON on stdout.

use actinv_data::{covariance, library};
use sha2::{Digest, Sha256};
use std::path::PathBuf;

fn sha256_file(path: &std::path::Path) -> Result<String, String> {
    let bytes =
        std::fs::read(path).map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    Ok(format!("{:x}", Sha256::digest(&bytes)))
}

fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 5 {
        eprintln!("usage: p20_collapse_probe LIBRARY.npz COVARIANCE.cov.npz FLUX_CSV ROWS_CSV");
        std::process::exit(2);
    }
    let library_path = PathBuf::from(&args[1]);
    let covariance_path = PathBuf::from(&args[2]);
    let flux: Vec<f64> = std::fs::read_to_string(&args[3])
        .map_err(|error| format!("cannot read flux {}: {error}", args[3]))?
        .split(',')
        .map(|field| {
            field
                .trim()
                .parse::<f64>()
                .map_err(|_| format!("invalid flux value {field:?}"))
        })
        .collect::<Result<_, _>>()?;
    let rows: Vec<usize> = std::fs::read_to_string(&args[4])
        .map_err(|error| format!("cannot read rows {}: {error}", args[4]))?
        .split(',')
        .filter(|field| !field.trim().is_empty())
        .map(|field| {
            field
                .trim()
                .parse::<usize>()
                .map_err(|_| format!("invalid row index {field:?}"))
        })
        .collect::<Result<_, _>>()?;
    let activation = library::read_npz_after_sha256_verification(
        library_path.to_str().ok_or("library path is not UTF-8")?,
    )?;
    let cov = covariance::read_npz(&covariance_path)?;
    let collapsed = cov.collapse(&activation, &flux, &rows)?;
    let out = serde_json::json!({
        "schema": "actinv-p20-collapse-1",
        "library_sha256": sha256_file(&library_path)?,
        "covariance_sha256": sha256_file(&covariance_path)?,
        "collapsed": collapsed,
    });
    println!(
        "{}",
        serde_json::to_string(&out).map_err(|error| error.to_string())?
    );
    Ok(())
}
