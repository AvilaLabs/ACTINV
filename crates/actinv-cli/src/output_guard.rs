//! One guard for every command that writes a result file: the output must
//! not resolve to a file the same command reads. Without it
//! `actinv run problem.json problem.json` replaces the problem with its
//! result after the solve.

use std::path::{Path, PathBuf};

/// A path with symlinks and `..` resolved; a file that does not exist yet is
/// resolved through its directory.
fn resolved(path: &Path) -> PathBuf {
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

/// True when both paths name one existing file (a hard link included).
fn same_existing_file(a: &Path, b: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if let (Ok(ma), Ok(mb)) = (std::fs::metadata(a), std::fs::metadata(b)) {
            return ma.dev() == mb.dev() && ma.ino() == mb.ino();
        }
    }
    resolved(a) == resolved(b)
}

/// Refuse when `out` is the same file as any of `inputs`. An output that
/// does not exist yet cannot be an input, so the paths are compared after
/// resolving through the output's directory.
pub fn refuse_input_overwrite(out: &str, inputs: &[&str]) -> Result<(), String> {
    let out_path = Path::new(out);
    for input in inputs {
        if same_existing_file(out_path, Path::new(input)) {
            return Err(format!(
                "{out} is the input {input}: choose another OUT, the input would be overwritten"
            ));
        }
    }
    Ok(())
}

/// `refuse_input_overwrite` for an optional output; `None` and `-` (stdout)
/// write no file.
pub fn refuse_optional(out: Option<&str>, inputs: &[&str]) -> Result<(), String> {
    match out {
        Some(out) if out != "-" => refuse_input_overwrite(out, inputs),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("actinv-guard-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn same_path_is_refused() {
        let dir = scratch("same");
        let p = dir.join("problem.json");
        std::fs::write(&p, "{}").unwrap();
        let s = p.to_str().unwrap();
        let e = refuse_input_overwrite(s, &[s]).unwrap_err();
        assert!(e.contains("would be overwritten"), "{e}");
        // an unnormalised spelling of the same file
        let dotted = dir.join(".").join("problem.json");
        assert!(refuse_input_overwrite(dotted.to_str().unwrap(), &[s]).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn symlink_and_hard_link_are_refused() {
        let dir = scratch("link");
        let p = dir.join("problem.json");
        std::fs::write(&p, "{}").unwrap();
        let hard = dir.join("hard.json");
        std::fs::hard_link(&p, &hard).unwrap();
        assert!(refuse_input_overwrite(hard.to_str().unwrap(), &[p.to_str().unwrap()]).is_err());
        #[cfg(unix)]
        {
            let soft = dir.join("soft.json");
            std::os::unix::fs::symlink(&p, &soft).unwrap();
            assert!(
                refuse_input_overwrite(soft.to_str().unwrap(), &[p.to_str().unwrap()]).is_err()
            );
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn distinct_or_new_output_is_allowed() {
        let dir = scratch("ok");
        let p = dir.join("problem.json");
        std::fs::write(&p, "{}").unwrap();
        let fresh = dir.join("result.json");
        assert!(refuse_input_overwrite(fresh.to_str().unwrap(), &[p.to_str().unwrap()]).is_ok());
        assert!(refuse_optional(None, &["x"]).is_ok());
        assert!(refuse_optional(Some("-"), &["-"]).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
