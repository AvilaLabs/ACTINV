//! P132a: FISPACT-II input deck -> `actinv-spec-1` problem importer, command-line front end.
//!
//! `actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES] [--library LIB.npz]
//! [--decay DECAY] [--covariance COV.npz] [--strict]`
//!
//! The conversion itself (lexer, keyword table, mapping, report) is `actinv_core::fispact_import`,
//! shared with the browser build and re-exported here. This module reads the deck, the files file
//! and the fluxes file from disk, resolves their paths as FISPACT-II does, writes `OUT.json` and
//! `OUT.import.json`, and implements `--strict`.

use actinv_core::flux::sha256_file;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

pub use actinv_core::fispact_import::{
    convert, lex, load_flux_text, parse_files_file, Conversion, Counts, CovarianceRef,
    ImportContext, Lexed, LoadedFlux, Status, Tok, IRRADIATES_WITHOUT_FLUXES,
};

fn load_flux(path: &Path) -> Result<LoadedFlux, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read fluxes file {}: {error}", path.display()))?;
    load_flux_text(&path.display().to_string(), &text)
}

/// Where the report goes: `OUT.json` -> `OUT.import.json`.
pub fn report_path(out: &str) -> String {
    match out.strip_suffix(".json") {
        Some(stem) => format!("{stem}.import.json"),
        None => format!("{out}.import.json"),
    }
}

#[derive(Debug, Default)]
pub struct CliArgs {
    pub deck: String,
    pub out: String,
    pub fluxes: Option<String>,
    pub files: Option<String>,
    pub library: Option<String>,
    pub decay: Option<String>,
    pub covariance: Option<String>,
    pub strict: bool,
}

pub const USAGE: &str = "usage: actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES] [--library LIB.npz] [--decay DECAY] [--covariance COV.npz] [--strict]";

pub fn parse_cli(args: &[String]) -> Result<CliArgs, String> {
    let mut cli = CliArgs::default();
    let mut positional = Vec::new();
    let mut index = 0;
    while index < args.len() {
        let arg = args[index].as_str();
        match arg {
            "--strict" => {
                cli.strict = true;
                index += 1;
            }
            "--fluxes" | "--files" | "--library" | "--decay" | "--covariance" => {
                let value = args
                    .get(index + 1)
                    .ok_or_else(|| format!("option {arg} has no value"))?
                    .clone();
                let slot = match arg {
                    "--fluxes" => &mut cli.fluxes,
                    "--files" => &mut cli.files,
                    "--library" => &mut cli.library,
                    "--decay" => &mut cli.decay,
                    _ => &mut cli.covariance,
                };
                if slot.replace(value).is_some() {
                    return Err(format!("duplicate option {arg}"));
                }
                index += 2;
            }
            other if other.starts_with("--") => return Err(format!("unknown option {other}")),
            other => {
                positional.push(other.to_string());
                index += 1;
            }
        }
    }
    let [deck, out] = <[String; 2]>::try_from(positional).map_err(|_| USAGE.to_string())?;
    cli.deck = deck;
    cli.out = out;
    Ok(cli)
}

/// A path with symlinks and `..` resolved; a file that does not exist yet is resolved through its
/// directory.
fn resolved_path(path: &Path) -> PathBuf {
    if let Ok(real) = path.canonicalize() {
        return real;
    }
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    match (parent.canonicalize(), path.file_name()) {
        (Ok(parent), Some(name)) => parent.join(name),
        _ => path.to_path_buf(),
    }
}

/// First existing candidate among the bases (the files file's directory, the deck's directory,
/// then the working directory), as FISPACT-II resolves paths from the run directory.
fn resolve_existing(value: &str, bases: &[&Path]) -> Option<PathBuf> {
    let direct = Path::new(value);
    if direct.is_absolute() {
        return direct.is_file().then(|| direct.to_path_buf());
    }
    bases
        .iter()
        .map(|base| base.join(value))
        .chain(std::iter::once(direct.to_path_buf()))
        .find(|candidate| candidate.is_file())
}

/// Run the command. Returns the one-line summary to print.
pub fn run(cli: &CliArgs) -> Result<String, String> {
    let deck_path = Path::new(&cli.deck);
    // a deck is data: Latin-1 comments or a stray byte must not stop the import
    let deck_bytes =
        std::fs::read(deck_path).map_err(|error| format!("cannot read {}: {error}", cli.deck))?;
    let lossy_utf8 = std::str::from_utf8(&deck_bytes).is_err();
    let deck_text = String::from_utf8_lossy(&deck_bytes).into_owned();
    let report_file = report_path(&cli.out);
    let deck_real = resolved_path(deck_path);
    for target in [cli.out.as_str(), report_file.as_str()] {
        if resolved_path(Path::new(target)) == deck_real {
            return Err(format!(
                "{target} is the deck itself: choose another OUT, the deck would be overwritten"
            ));
        }
    }
    let deck_dir = deck_path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or(Path::new("."));

    // files file: explicit, else `<deck stem>_files` or `files` beside the deck
    let files_path: Option<PathBuf> = match &cli.files {
        Some(path) => Some(PathBuf::from(path)),
        None => {
            let stem = deck_path
                .file_stem()
                .map(|stem| stem.to_string_lossy().into_owned())
                .unwrap_or_default();
            [format!("{stem}_files"), "files".to_string()]
                .iter()
                .map(|name| deck_dir.join(name))
                .find(|candidate| candidate.is_file())
        }
    };
    let (files_entries, files_dir) = match &files_path {
        Some(path) => {
            let text = std::fs::read_to_string(path)
                .map_err(|error| format!("cannot read files file {}: {error}", path.display()))?;
            let dir = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .unwrap_or(Path::new("."))
                .to_path_buf();
            (parse_files_file(&text), dir)
        }
        None => (Vec::new(), deck_dir.to_path_buf()),
    };

    // a fluxes entry that does not resolve matters only to a deck that irradiates
    let mut unresolved_fluxes: Option<String> = None;
    let flux_path: Option<PathBuf> = match &cli.fluxes {
        Some(path) => Some(PathBuf::from(path)),
        None => match files_entries.iter().rev().find(|(key, _)| key == "fluxes") {
            Some((_, value)) => {
                let found = resolve_existing(value, &[files_dir.as_path(), deck_dir]);
                if found.is_none() {
                    unresolved_fluxes = Some(format!("the fluxes file '{value}' named by the files file was not found; pass --fluxes FILE"));
                }
                found
            }
            None => None,
        },
    };
    let flux = flux_path.as_deref().map(load_flux).transpose()?;
    let covariance = match &cli.covariance {
        Some(path) => Some(CovarianceRef {
            path: path.clone(),
            sha256: sha256_file(path)?,
        }),
        None => None,
    };
    let ctx = ImportContext {
        flux,
        library: cli.library.clone(),
        decay: cli.decay.clone(),
        covariance,
    };

    let conversion = convert(&deck_text, &ctx).map_err(|error| match &unresolved_fluxes {
        Some(note) if error.starts_with(IRRADIATES_WITHOUT_FLUXES) => format!("{error}; {note}"),
        _ => error,
    })?;
    let mut report = conversion.report;
    if let Value::Object(map) = &mut report {
        map.insert(
            "deck".into(),
            json!({
                "path": cli.deck,
                "sha256": sha256_file(deck_path)?,
                "lossy_utf8": lossy_utf8,
            }),
        );
        map.insert("strict".into(), json!(cli.strict));
        map.insert("fluxes_file_unresolved".into(), json!(unresolved_fluxes));
        map.insert(
            "files_file".into(),
            match &files_path {
                Some(path) => json!({
                    "path": path.display().to_string(),
                    "entries": files_entries
                        .iter()
                        .map(|(key, value)| json!({"key": key, "path": value}))
                        .collect::<Vec<_>>(),
                    "note": "xs_endf, dk_endf and the other FISPACT-II data entries are listed for reference and never read; the library and decay data come from the spec",
                }),
                None => Value::Null,
            },
        );
    }
    let write = |path: &str, value: &Value| -> Result<(), String> {
        let text = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
        std::fs::write(path, text + "\n").map_err(|error| format!("cannot write {path}: {error}"))
    };
    write(&report_file, &report)?;
    let counts = conversion.counts;
    if cli.strict && counts.unsupported > 0 {
        // a spec from an earlier run must not pass for the result of this one
        match std::fs::remove_file(&cli.out) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(format!("cannot remove the stale {}: {error}", cli.out));
            }
        }
        let list: Vec<String> = conversion
            .unsupported
            .iter()
            .map(|(keyword, line)| format!("{keyword} (line {line})"))
            .collect();
        return Err(format!(
            "--strict: {} unsupported keyword(s): {}; see {report_file}",
            counts.unsupported,
            list.join(", ")
        ));
    }
    write(&cli.out, &conversion.spec)?;
    let required = report["required_additions"].as_array().map_or(0, Vec::len);
    let needs = if required > 0 {
        format!(
            "; {required} section(s) must be added before it validates (see required_additions)"
        )
    } else {
        String::new()
    };
    Ok(format!(
        "imported {} -> {} ({} steps; {} mapped, {} approximated, {} noted, {} unsupported){needs}; report: {report_file}",
        cli.deck,
        cli.out,
        report["steps"],
        counts.mapped,
        counts.approximated,
        counts.noted,
        counts.unsupported
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A deck with no irradiation: it needs no fluxes file.
    const COOLING_ONLY: &str =
        "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 0\nTIME 1 DAYS ATOMS\nEND\n";

    fn p140_scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("actinv-p140-if-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn p140_cli(deck: &Path, out: &Path) -> CliArgs {
        CliArgs {
            deck: deck.display().to_string(),
            out: out.display().to_string(),
            library: Some("lib.npz".into()),
            decay: Some("decay.endf".into()),
            ..CliArgs::default()
        }
    }

    #[test]
    fn command_line_parsing_and_report_path() {
        let args: Vec<String> = [
            "deck.i",
            "out.json",
            "--strict",
            "--fluxes",
            "f",
            "--library",
            "l.npz",
        ]
        .iter()
        .map(|word| word.to_string())
        .collect();
        let cli = parse_cli(&args).unwrap();
        assert_eq!(
            (cli.deck.as_str(), cli.out.as_str()),
            ("deck.i", "out.json")
        );
        assert!(cli.strict);
        assert_eq!(cli.fluxes.as_deref(), Some("f"));
        assert_eq!(cli.library.as_deref(), Some("l.npz"));
        assert!(parse_cli(&["deck.i".to_string()]).is_err());
        assert!(parse_cli(&["a".into(), "b".into(), "--bogus".into()]).is_err());
        assert!(parse_cli(&["a".into(), "b".into(), "--fluxes".into()]).is_err());
        assert_eq!(report_path("out.json"), "out.import.json");
        assert_eq!(report_path("out"), "out.import.json");
    }

    #[test]
    fn load_flux_reads_a_file_through_the_text_parser() {
        let dir = p140_scratch("load-flux");
        let path = dir.join("fluxes");
        std::fs::write(&path, " 1.0 2.0 3.0\n 0.5\nPosition #2\n").unwrap();
        let loaded = load_flux(&path);
        let missing = load_flux(&dir.join("absent"));
        let _ = std::fs::remove_dir_all(&dir);
        let loaded = loaded.unwrap();
        assert_eq!(loaded.path, path.display().to_string());
        assert_eq!(loaded.values, vec![1.0, 2.0, 3.0]);
        assert_eq!(loaded.wall_loading_mw_m2, 0.5);
        assert!(missing.unwrap_err().starts_with("cannot read fluxes file "));
    }

    #[test]
    fn p140_if_f9_a_deck_with_a_non_utf8_comment_byte_imports() {
        let dir = p140_scratch("f9-latin1");
        let deck = dir.join("deck.i");
        let out = dir.join("out.json");
        let mut bytes = b"<< dose in \xb5Sv/h >>\n".to_vec();
        bytes.extend_from_slice(COOLING_ONLY.as_bytes());
        std::fs::write(&deck, bytes).unwrap();
        let result = run(&p140_cli(&deck, &out));
        let written = out.exists();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(result.is_ok(), "{result:?}");
        assert!(written);
    }

    // ---- F10: an unresolvable fluxes entry matters only to a deck that irradiates ----

    #[test]
    fn p140_if_f10_a_missing_fluxes_file_is_ignored_by_a_cooling_only_deck() {
        let dir = p140_scratch("f10-cooling");
        let deck = dir.join("deck.i");
        let files = dir.join("files");
        let out = dir.join("out.json");
        std::fs::write(&deck, COOLING_ONLY).unwrap();
        std::fs::write(&files, "fluxes /nonexistent/p140/fluxes\n").unwrap();
        let mut cli = p140_cli(&deck, &out);
        cli.files = Some(files.display().to_string());
        let result = run(&cli);
        let written = out.exists();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(result.is_ok(), "{result:?}");
        assert!(written);
    }

    #[test]
    fn p140_if_f10_a_missing_fluxes_file_still_stops_an_irradiating_deck() {
        let dir = p140_scratch("f10-irradiating");
        let deck = dir.join("deck.i");
        let files = dir.join("files");
        let out = dir.join("out.json");
        std::fs::write(
            &deck,
            "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n",
        )
        .unwrap();
        std::fs::write(&files, "fluxes /nonexistent/p140/fluxes\n").unwrap();
        let mut cli = p140_cli(&deck, &out);
        cli.files = Some(files.display().to_string());
        let result = run(&cli);
        let written = out.exists();
        let _ = std::fs::remove_dir_all(&dir);
        let error = result.expect_err("an irradiating deck needs its spectrum");
        assert!(error.contains("flux"), "{error}");
        assert!(!written);
    }

    // ---- F11: --strict leaves no stale spec; OUT must not be the deck ----

    #[test]
    fn p140_if_f11_a_strict_failure_removes_a_stale_output() {
        let dir = p140_scratch("f11-strict");
        let deck = dir.join("deck.i");
        let out = dir.join("out.json");
        std::fs::write(
            &deck,
            "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFROBNICATE\nFLUX 0\nTIME 1 DAYS ATOMS\nEND\n",
        )
        .unwrap();
        std::fs::write(&out, "{\"stale\": true}\n").unwrap();
        let mut cli = p140_cli(&deck, &out);
        cli.strict = true;
        let result = run(&cli);
        let stale_left = out.exists();
        let report_written = dir.join("out.import.json").exists();
        let _ = std::fs::remove_dir_all(&dir);
        let error = result.expect_err("--strict must fail on FROBNICATE");
        assert!(error.contains("--strict"), "{error}");
        assert!(report_written);
        assert!(
            !stale_left,
            "the previous OUT.json survived a failed --strict import"
        );
    }

    #[test]
    fn p140_if_f11_out_equal_to_deck_is_refused() {
        let dir = p140_scratch("f11-same");
        let deck = dir.join("deck.i");
        std::fs::write(&deck, COOLING_ONLY).unwrap();
        let result = run(&p140_cli(&deck, &deck));
        let after = std::fs::read_to_string(&deck).unwrap_or_default();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(result.is_err(), "{result:?}");
        assert_eq!(after, COOLING_ONLY, "the deck was overwritten by the spec");
    }

    #[test]
    fn p140_if_f11_out_aliasing_the_deck_through_a_path_alias_is_refused() {
        let dir = p140_scratch("f11-alias");
        std::fs::create_dir_all(dir.join("sub")).unwrap();
        let deck = dir.join("deck.i");
        std::fs::write(&deck, COOLING_ONLY).unwrap();
        let alias = dir.join("sub").join("..").join("deck.i");
        let result = run(&p140_cli(&deck, &alias));
        let after = std::fs::read_to_string(&deck).unwrap_or_default();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(result.is_err(), "{result:?}");
        assert_eq!(after, COOLING_ONLY, "the deck was overwritten by the spec");
    }
}
