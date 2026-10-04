//! P11-G4 probe for the production S C S^T validation path.

use actinv_core::uncertainty::propagated_variance;

fn values(text: &str) -> Result<Vec<f64>, String> {
    text.split(',')
        .map(|value| {
            value
                .parse::<f64>()
                .map_err(|_| format!("invalid floating-point value '{value}'"))
        })
        .collect()
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() != 3 {
        eprintln!("usage: uncertainty_probe SENSITIVITIES_CSV COVARIANCE_CSV");
        std::process::exit(2);
    }
    let result = values(&arguments[1]).and_then(|sensitivity| {
        values(&arguments[2]).and_then(|covariance| propagated_variance(&sensitivity, &covariance))
    });
    match result {
        Ok((variance, residue)) => {
            println!(
                "{{\"variance\":{variance:.17e},\"negative_roundoff_removed\":{residue:.17e}}}"
            );
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
