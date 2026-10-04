//! P11-G5 probe for the explicit immutable `PreparedRun` entry path.

use actinv_core::{run::PreparedRun, spec::Spec};

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() != 3 {
        eprintln!("usage: prepared_probe SPEC.json OUT.json");
        std::process::exit(2);
    }
    let result = (|| -> Result<(), String> {
        let text = std::fs::read_to_string(&arguments[1]).map_err(|error| error.to_string())?;
        let spec = Spec::from_json(&text)?;
        let prepared = PreparedRun::prepare(&spec)?;
        let result = prepared.run(&spec, "prepared")?;
        let output = serde_json::to_string_pretty(&result).map_err(|error| error.to_string())?;
        std::fs::write(&arguments[2], output).map_err(|error| error.to_string())
    })();
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
