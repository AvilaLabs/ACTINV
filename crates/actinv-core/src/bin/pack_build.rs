//! Splits an activation library and its decay sublibraries into a pack (`actinv_core::pack`):
//! `OUTDIR/graph.json`, one NPZ per target under `OUTDIR/targets/` and one record per state under
//! `OUTDIR/decay/`.
//!
//!   pack_build LIBRARY.npz OUTDIR DECAY_PRIMARY [DECAY_FALLBACK]
//!
//! `ACTINV_PACK_IDS` gives the sources' catalog ids, comma separated in the same order (library,
//! then decay); without it each id is its file name.
//!
//! The index is read from beside the library (`LIBRARY_index.json`).

use actinv_core::pack::{self, PackNames, SourceName};

fn file_name(path: &str) -> String {
    std::path::Path::new(path)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [library, out, decay @ ..] = args.as_slice() else {
        eprintln!("usage: pack_build LIBRARY.npz OUTDIR DECAY_PRIMARY [DECAY_FALLBACK]");
        std::process::exit(2);
    };
    if !(1..=2).contains(&decay.len()) {
        eprintln!("usage: pack_build LIBRARY.npz OUTDIR DECAY_PRIMARY [DECAY_FALLBACK]");
        std::process::exit(2);
    }
    let result = (|| -> Result<(), String> {
        let npz = std::fs::read(library).map_err(|e| format!("{library}: {e}"))?;
        let index_path = format!(
            "{}_index.json",
            library
                .strip_suffix(".npz")
                .ok_or("the library must be a .npz")?
        );
        let index =
            std::fs::read_to_string(&index_path).map_err(|e| format!("{index_path}: {e}"))?;
        let mut decay_texts = Vec::new();
        for path in decay {
            decay_texts.push(std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?);
        }
        let decay_texts: Vec<&str> = decay_texts.iter().map(String::as_str).collect();
        let paths: Vec<&String> = std::iter::once(library).chain(decay).collect();
        let ids: Vec<String> = match std::env::var("ACTINV_PACK_IDS") {
            Ok(ids) => ids.split(',').map(str::to_owned).collect(),
            Err(_) => paths.iter().map(|path| file_name(path)).collect(),
        };
        if ids.len() != paths.len() {
            return Err("ACTINV_PACK_IDS needs one id per source".into());
        }
        let mut sources = paths.iter().zip(ids).map(|(path, id)| SourceName {
            id,
            file: file_name(path),
        });
        let names = PackNames {
            library: sources.next().expect("the library is named"),
            decay: sources.collect(),
        };
        let (graph, files) = pack::build(&npz, &index, &decay_texts, names)?;
        let out = std::path::Path::new(out);
        for directory in ["targets", "decay/0", "decay/1"] {
            std::fs::create_dir_all(out.join(directory)).map_err(|e| e.to_string())?;
        }
        let mut total = 0usize;
        for (name, bytes) in &files {
            total += bytes.len();
            std::fs::write(out.join(name), bytes).map_err(|e| format!("{name}: {e}"))?;
        }
        let graph_text = serde_json::to_string(&graph).map_err(|e| e.to_string())?;
        std::fs::write(out.join("graph.json"), &graph_text).map_err(|e| e.to_string())?;
        eprintln!(
            "{} targets, {} files, {total} bytes, graph {} bytes",
            graph.targets.len(),
            files.len(),
            graph_text.len()
        );
        Ok(())
    })();
    if let Err(error) = result {
        eprintln!("pack_build: {error}");
        std::process::exit(1);
    }
}
