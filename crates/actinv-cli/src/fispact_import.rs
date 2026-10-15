//! P132a: FISPACT-II input deck -> `actinv-spec-1` problem importer.
//!
//! `actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES] [--library LIB.npz]
//! [--decay DECAY] [--covariance COV.npz] [--strict]`
//!
//! The deck is read with FISPACT-II's own lexical rules (free-format keywords, `<< ... >>`
//! comments that may span lines, `*` text lines, keywords recognised case-insensitively by
//! their leading characters) and interpreted keyword by keyword in order. Nothing is dropped
//! silently: every keyword lands in the `OUT.import.json` report with its line, the section of
//! the deck it appeared in and one status:
//!
//! * `mapped`       translated into a spec field;
//! * `approximated` translated with a stated caveat;
//! * `noted`        no effect on the calculated physics (output selectors, library bookkeeping);
//! * `unsupported`  would change the physics but has no ACTINV equivalent (or is not a FISPACT-II
//!   keyword at all). `--strict` fails the import when any exists.
//!
//! Honesty: the importer converts the problem description, not FISPACT-II's nuclear data. The
//! activation library and decay data of the resulting spec are ACTINV's, and a deck's own
//! `xs_endf` / `dk_endf` entries are reported but never read.
//!
//! Some keywords map to spec sections that need a data file no deck names (`POWER` needs a
//! `kerma.table`). The importer then emits what the deck states and lists the missing section
//! under `required_additions` in the report; the spec is not runnable until it is added.

use actinv_core::flux::{read_fispact_fluxes, sha256_file};
use actinv_core::spec::{Spec, POINT_SOURCE_MIN_DISTANCE_M};
use actinv_data::activation::Projectile;
use actinv_data::composition::{material_key, MaterialKey, NA};
use actinv_data::groups::{GroupStructure, NAMED_STRUCTURES};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Upper bound on schedule steps after PULSE expansion.
const MAX_STEPS: usize = 100_000;
/// Catalog identifier of the covariance sidecar paired with the default neutron library.
const DEFAULT_COVARIANCE: &str = "tendl-2025-neutron-709g-covariance";

// ---------------------------------------------------------------------------------------------
// Lexer
// ---------------------------------------------------------------------------------------------

/// One lexical item of a deck: a whitespace-delimited word, or a whole `*` text line.
#[derive(Debug, Clone, PartialEq)]
pub struct Tok {
    /// the word; for a `*` line the text after the `*`, trimmed
    pub text: String,
    /// one-based line number
    pub line: usize,
    /// true for a `*` text line (a title); `<<` has no meaning inside it
    pub star: bool,
}

#[derive(Debug)]
pub struct Lexed {
    pub tokens: Vec<Tok>,
    /// an unterminated `<<` comment; fatal only when the deck had not ended before it
    pub error: Option<String>,
}

/// Split a deck into words and `*` text lines. `<< ... >>` comments are removed wherever they
/// occur and may span lines; a line whose first non-blank character is `*` is text, inside which
/// `<<` is not a comment (manual 4.94).
pub fn lex(text: &str) -> Lexed {
    let mut tokens = Vec::new();
    let mut comment_from: Option<usize> = None;
    for (index, raw) in text.lines().enumerate() {
        let line = index + 1;
        let mut rest = raw;
        if comment_from.is_none() {
            if let Some(title) = rest.trim_start().strip_prefix('*') {
                tokens.push(Tok {
                    text: title.trim().to_string(),
                    line,
                    star: true,
                });
                continue;
            }
        }
        loop {
            if comment_from.is_some() {
                match rest.find(">>") {
                    Some(position) => {
                        rest = &rest[position + 2..];
                        comment_from = None;
                    }
                    None => break,
                }
                continue;
            }
            rest = rest.trim_start();
            if rest.is_empty() {
                break;
            }
            if let Some(after) = rest.strip_prefix("<<") {
                comment_from = Some(line);
                rest = after;
                continue;
            }
            let end = rest
                .char_indices()
                .find(|(position, character)| {
                    character.is_whitespace() || rest[*position..].starts_with("<<")
                })
                .map(|(position, _)| position)
                .unwrap_or(rest.len());
            tokens.push(Tok {
                text: rest[..end].to_string(),
                line,
                star: false,
            });
            rest = &rest[end..];
        }
    }
    let error =
        comment_from.map(|line| format!("line {line}: '<<' comment is never closed by '>>'"));
    Lexed { tokens, error }
}

/// A Fortran-style real: `1.0E-3`, `1.E3`, `2D5`, and the exponent letter dropped (`1.0+10`).
fn parse_number(token: &str) -> Option<f64> {
    let normalised = token.replace(['D', 'd'], "E");
    let value = normalised.parse::<f64>().ok().or_else(|| {
        let bytes = normalised.as_bytes();
        let split = (1..bytes.len()).find(|&index| {
            (bytes[index] == b'+' || bytes[index] == b'-')
                && (bytes[index - 1].is_ascii_digit() || bytes[index - 1] == b'.')
        })?;
        format!("{}E{}", &normalised[..split], &normalised[split..])
            .parse::<f64>()
            .ok()
    })?;
    value.is_finite().then_some(value)
}

/// A TIME unit word to the unit string `actinv_core::spec::parse_duration` understands.
fn time_unit(token: &str) -> Option<&'static str> {
    let upper = token.to_ascii_uppercase();
    let key: String = upper.chars().take(4).collect();
    match key.as_str() {
        "SEC" | "SECS" | "SECO" => Some("s"),
        "MIN" | "MINS" | "MINU" => Some("min"),
        "HOU" | "HOUR" | "HR" | "HRS" => Some("h"),
        "DAY" | "DAYS" => Some("d"),
        "YEAR" | "YR" | "YRS" => Some("y"),
        _ => None,
    }
}

// ---------------------------------------------------------------------------------------------
// Keyword table
// ---------------------------------------------------------------------------------------------

/// What follows a keyword on the deck.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Arity {
    /// nothing
    Zero,
    /// numeric words only, as many as present
    Numbers,
    /// every word up to the next keyword (nuclide names, sub-keywords, counted lists)
    Words,
    /// parsed by a dedicated routine (TIME, MASS, FUEL, FISPACT, END, PULSE)
    Special,
}

const KEYWORDS: &[(&str, Arity)] = &[
    ("ALLDISPEN", Arity::Numbers),
    ("ATDISPEN", Arity::Words),
    ("ATOMS", Arity::Zero),
    ("ATWO", Arity::Zero),
    ("BREMSSTRAHLUNG", Arity::Words),
    ("CLEAR", Arity::Zero),
    ("CLOBBER", Arity::Zero),
    ("CNVTYPE", Arity::Numbers),
    ("COMPRESSXS", Arity::Numbers),
    ("COVARIANCE", Arity::Zero),
    ("CULTAB", Arity::Zero),
    ("CUMFYLD", Arity::Zero),
    ("DENSITY", Arity::Numbers),
    ("DEPLETION", Arity::Words),
    ("DOSE", Arity::Numbers),
    ("END", Arity::Special),
    ("ENDPULSE", Arity::Zero),
    ("ERROR", Arity::Words),
    ("EXTRACTXS", Arity::Words),
    ("FISCHOOSE", Arity::Words),
    ("FISPACT", Arity::Special),
    ("FISYIELD", Arity::Words),
    ("FLUX", Arity::Numbers),
    ("FUEL", Arity::Special),
    ("FULLXS", Arity::Zero),
    ("GENERIC", Arity::Numbers),
    ("GETDECAY", Arity::Numbers),
    ("GETXS", Arity::Numbers),
    ("GRAPH", Arity::Numbers),
    ("GROUP", Arity::Numbers),
    ("GRPCONVERT", Arity::Numbers),
    ("HALF", Arity::Zero),
    ("HAZARDS", Arity::Zero),
    ("INDEXPATH", Arity::Zero),
    ("IRON", Arity::Zero),
    ("JSON", Arity::Zero),
    ("LIBVERSION", Arity::Numbers),
    ("LIMGRP", Arity::Numbers),
    ("LOGLEVEL", Arity::Numbers),
    ("LOOKAHEAD", Arity::Zero),
    ("MASS", Arity::Special),
    ("MCSAMPLE", Arity::Numbers),
    ("MIND", Arity::Numbers),
    ("MONITOR", Arity::Numbers),
    ("NOCOMP", Arity::Zero),
    ("NOERROR", Arity::Zero),
    ("NOFISS", Arity::Zero),
    ("NOSORT", Arity::Zero),
    ("NOSTABLE", Arity::Zero),
    ("NOT1", Arity::Zero),
    ("NOT2", Arity::Zero),
    ("NOT3", Arity::Zero),
    ("NOT4", Arity::Zero),
    ("NOXRAY", Arity::Zero),
    ("NUCGRAPH", Arity::Numbers),
    ("OVER", Arity::Words),
    ("PARTITION", Arity::Words),
    ("PATH", Arity::Words),
    ("PATHRESET", Arity::Numbers),
    ("POWER", Arity::Words),
    ("PRINTLIB", Arity::Numbers),
    ("PROBTABLE", Arity::Numbers),
    ("PROJECTILE", Arity::Numbers),
    ("PULSE", Arity::Special),
    ("READGG", Arity::Zero),
    ("READSF", Arity::Zero),
    ("RESULT", Arity::Words),
    ("ROUTES", Arity::Words),
    ("SAVELINES", Arity::Zero),
    ("SENSITIVITY", Arity::Words),
    ("SORTDOMINANT", Arity::Numbers),
    ("SPECTRUM", Arity::Zero),
    ("SPEK", Arity::Zero),
    ("SPLIT", Arity::Numbers),
    ("SSFCHOOSE", Arity::Words),
    ("SSFDILUTION", Arity::Words),
    ("SSFFUEL", Arity::Words),
    ("SSFGEOMETRY", Arity::Numbers),
    ("SSFMASS", Arity::Words),
    ("STEP", Arity::Zero),
    ("TAB1", Arity::Numbers),
    ("TAB2", Arity::Numbers),
    ("TAB3", Arity::Numbers),
    ("TAB4", Arity::Numbers),
    ("TIME", Arity::Special),
    ("TOLERANCE", Arity::Numbers),
    ("UNCERTAINTY", Arity::Numbers),
    ("UNCTYPE", Arity::Numbers),
    ("USEFISSION", Arity::Zero),
    ("USESPALLATION", Arity::Words),
    ("USEXSEXTRA", Arity::Zero),
    ("WALL", Arity::Numbers),
    ("XSTHRESHOLD", Arity::Numbers),
    ("ZERO", Arity::Zero),
];

/// Keywords the interpreter handles itself; every other table entry has a [`simple_disposition`].
#[cfg(test)]
const HANDLED: &[&str] = &[
    "ATOMS",
    "ATWO",
    "BREMSSTRAHLUNG",
    "CNVTYPE",
    "DENSITY",
    "DOSE",
    "END",
    "ENDPULSE",
    "FISPACT",
    "FLUX",
    "FUEL",
    "GETDECAY",
    "GETXS",
    "GRPCONVERT",
    "GROUP",
    "LIMGRP",
    "MASS",
    "MIND",
    "NOERROR",
    "NOSTABLE",
    "OVER",
    "PARTITION",
    "PATH",
    "POWER",
    "PROJECTILE",
    "PULSE",
    "ROUTES",
    "SORTDOMINANT",
    "SPECTRUM",
    "STEP",
    "TIME",
    "UNCERTAINTY",
    "WALL",
    "ZERO",
];

/// Resolve a deck word to a keyword. FISPACT-II reads the leading characters of a keyword, so
/// `HAZA`, `UNCERT` and `TOLER` are the usual spellings: an exact name wins, then a unique name
/// the word is a prefix of (four or more characters), then a unique name sharing the first four
/// characters.
fn resolve_keyword(token: &str) -> Option<(&'static str, Arity)> {
    let upper = token.to_ascii_uppercase();
    if let Some(entry) = KEYWORDS.iter().find(|(name, _)| *name == upper) {
        return Some(*entry);
    }
    if upper.chars().count() < 4 {
        return None;
    }
    let prefixed: Vec<_> = KEYWORDS
        .iter()
        .filter(|(name, _)| name.starts_with(upper.as_str()))
        .collect();
    if prefixed.len() == 1 {
        return Some(*prefixed[0]);
    }
    let head: String = upper.chars().take(4).collect();
    let sharing: Vec<_> = KEYWORDS
        .iter()
        .filter(|(name, _)| name.len() >= 4 && name.starts_with(head.as_str()))
        .collect();
    (sharing.len() == 1).then(|| *sharing[0])
}

// ---------------------------------------------------------------------------------------------
// Parser: tokens -> commands, with PULSE loops as a tree
// ---------------------------------------------------------------------------------------------

/// The section of the deck (manual section 4): library preparation up to FISPACT, initial
/// conditions up to the first TIME, then the inventory calculation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Phase {
    Library,
    Initial,
    Inventory,
}

impl Phase {
    fn name(self) -> &'static str {
        match self {
            Phase::Library => "library",
            Phase::Initial => "initial",
            Phase::Inventory => "inventory",
        }
    }
}

#[derive(Debug, Clone)]
struct Command {
    /// canonical keyword name; the upper-cased word itself when `known` is false; `*` for a
    /// text line outside FISPACT/END
    keyword: String,
    known: bool,
    line: usize,
    phase: Phase,
    /// raw argument words (for TIME the unit is already canonical)
    args: Vec<String>,
}

#[derive(Debug, Clone)]
enum Node {
    Cmd(Command),
    Pulse {
        cmd: Command,
        count: u64,
        body: Vec<Node>,
    },
}

struct Parser<'a> {
    tokens: &'a [Tok],
    pos: usize,
    phase: Phase,
    ended: bool,
}

impl<'a> Parser<'a> {
    fn peek_word(&self) -> Option<&'a Tok> {
        self.tokens.get(self.pos).filter(|token| !token.star)
    }

    fn take_numbers(&mut self, limit: usize) -> Vec<String> {
        let mut out = Vec::new();
        while out.len() < limit {
            match self.peek_word() {
                Some(token) if parse_number(&token.text).is_some() => {
                    out.push(token.text.clone());
                    self.pos += 1;
                }
                _ => break,
            }
        }
        out
    }

    fn take_words(&mut self) -> Vec<String> {
        let mut out = Vec::new();
        while let Some(token) = self.peek_word() {
            if resolve_keyword(&token.text).is_some() {
                break;
            }
            out.push(token.text.clone());
            self.pos += 1;
        }
        out
    }

    /// Exactly `count` further words (the body of a counted list).
    fn take_exact(
        &mut self,
        count: usize,
        keyword: &str,
        line: usize,
    ) -> Result<Vec<String>, String> {
        let mut out = Vec::with_capacity(count);
        for _ in 0..count {
            let token = self.peek_word().ok_or_else(|| {
                format!("line {line}: {keyword} lists fewer entries than its count declares")
            })?;
            out.push(token.text.clone());
            self.pos += 1;
        }
        Ok(out)
    }

    fn command(&self, keyword: &str, known: bool, line: usize, args: Vec<String>) -> Command {
        Command {
            keyword: keyword.to_string(),
            known,
            line,
            phase: self.phase,
            args,
        }
    }

    /// Parse until END, the end of input, or (at depth > 0) the ENDPULSE closing the loop.
    /// The flag is true when the block was closed by ENDPULSE.
    fn parse_nodes(&mut self, depth: usize) -> Result<(Vec<Node>, bool), String> {
        let mut nodes = Vec::new();
        while self.pos < self.tokens.len() && !self.ended {
            let token = self.tokens[self.pos].clone();
            self.pos += 1;
            if token.star {
                nodes.push(Node::Cmd(self.command(
                    "*",
                    true,
                    token.line,
                    vec![token.text],
                )));
                continue;
            }
            let Some((name, arity)) = resolve_keyword(&token.text) else {
                let args = self.take_numbers(usize::MAX);
                nodes.push(Node::Cmd(self.command(
                    &token.text.to_ascii_uppercase(),
                    false,
                    token.line,
                    args,
                )));
                continue;
            };
            match name {
                "ENDPULSE" => {
                    if depth == 0 {
                        return Err(format!(
                            "line {}: ENDPULSE without a matching PULSE",
                            token.line
                        ));
                    }
                    return Ok((nodes, true));
                }
                "PULSE" => {
                    let args = self.take_numbers(1);
                    let count = args
                        .first()
                        .and_then(|raw| parse_number(raw))
                        .filter(|value| *value >= 0.0 && value.fract() == 0.0 && *value <= 1.0e9)
                        .ok_or_else(|| {
                            format!(
                                "line {}: PULSE needs a whole-number repeat count",
                                token.line
                            )
                        })? as u64;
                    let cmd = self.command(name, true, token.line, args);
                    let (body, closed) = self.parse_nodes(depth + 1)?;
                    if !closed {
                        return Err(format!(
                            "line {}: PULSE is not closed by ENDPULSE before the deck ends",
                            token.line
                        ));
                    }
                    nodes.push(Node::Pulse { cmd, count, body });
                }
                "END" => {
                    // an END title line follows; the rest of the file is ignored
                    let mut args = Vec::new();
                    if let Some(title) = self.tokens.get(self.pos).filter(|next| next.star) {
                        args.push(title.text.clone());
                        self.pos += 1;
                    }
                    nodes.push(Node::Cmd(self.command(name, true, token.line, args)));
                    self.ended = true;
                }
                "FISPACT" => {
                    let mut args = Vec::new();
                    if let Some(title) = self.tokens.get(self.pos).filter(|next| next.star) {
                        args.push(title.text.clone());
                        self.pos += 1;
                    }
                    nodes.push(Node::Cmd(self.command(name, true, token.line, args)));
                    self.phase = Phase::Initial;
                }
                "TIME" => {
                    let mut args = self.take_numbers(1);
                    if args.is_empty() {
                        return Err(format!("line {}: TIME needs a duration", token.line));
                    }
                    if let Some(unit) = self.peek_word().and_then(|next| time_unit(&next.text)) {
                        args.push(unit.to_string());
                        self.pos += 1;
                    }
                    nodes.push(Node::Cmd(self.command(name, true, token.line, args)));
                    self.phase = Phase::Inventory;
                }
                "MASS" | "FUEL" => {
                    let mut args = self.take_numbers(if name == "MASS" { 2 } else { 1 });
                    let want = if name == "MASS" { 2 } else { 1 };
                    if args.len() != want {
                        return Err(format!(
                            "line {}: {name} needs {}",
                            token.line,
                            if name == "MASS" {
                                "a total mass and an element count"
                            } else {
                                "a nuclide count"
                            }
                        ));
                    }
                    let count = parse_number(&args[want - 1])
                        .filter(|value| *value >= 1.0 && value.fract() == 0.0 && *value <= 10_000.0)
                        .ok_or_else(|| {
                            format!(
                                "line {}: {name} count must be a positive whole number",
                                token.line
                            )
                        })? as usize;
                    args.extend(self.take_exact(2 * count, name, token.line)?);
                    nodes.push(Node::Cmd(self.command(name, true, token.line, args)));
                }
                _ => {
                    let args = match arity {
                        Arity::Zero | Arity::Special => Vec::new(),
                        Arity::Numbers => self.take_numbers(usize::MAX),
                        Arity::Words => self.take_words(),
                    };
                    nodes.push(Node::Cmd(self.command(name, true, token.line, args)));
                }
            }
        }
        Ok((nodes, false))
    }
}

// ---------------------------------------------------------------------------------------------
// Interpreter: commands -> spec fields + report entries
// ---------------------------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Mapped,
    Approximated,
    Noted,
    Unsupported,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Status::Mapped => "mapped",
            Status::Approximated => "approximated",
            Status::Noted => "noted",
            Status::Unsupported => "unsupported",
        }
    }
}

#[derive(Debug, Clone)]
struct Entry {
    keyword: String,
    line: usize,
    phase: Phase,
    status: Status,
    detail: String,
    args: String,
}

/// A group-flux file already read from disk.
#[derive(Debug, Clone)]
pub struct LoadedFlux {
    pub path: String,
    /// group values exactly as listed in the file: highest energy first
    pub values: Vec<f64>,
    pub wall_loading_mw_m2: f64,
    pub title: String,
}

#[derive(Debug, Clone)]
pub struct CovarianceRef {
    pub path: String,
    pub sha256: String,
}

/// Everything the conversion needs from outside the deck.
#[derive(Debug, Clone, Default)]
pub struct ImportContext {
    pub flux: Option<LoadedFlux>,
    pub library: Option<String>,
    pub decay: Option<String>,
    pub covariance: Option<CovarianceRef>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Counts {
    pub mapped: usize,
    pub approximated: usize,
    pub noted: usize,
    pub unsupported: usize,
}

pub struct Conversion {
    /// the `actinv-spec-1` document, plus a `summary` block when the deck asks for one
    pub spec: Value,
    /// the `actinv-fispact-import-report-1` document
    pub report: Value,
    pub counts: Counts,
    /// (keyword, line) of every unsupported entry
    pub unsupported: Vec<(String, usize)>,
}

enum MaterialDef {
    Mass {
        total_g: f64,
        elements: BTreeMap<String, f64>,
    },
    Fuel {
        total_g: f64,
        atoms_per_g: BTreeMap<String, f64>,
    },
}

struct Pending {
    value: f64,
    unit: &'static str,
    line: usize,
}

/// What fixes the irradiation level while a step runs.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Drive {
    /// FLUX: projectiles cm^-2 s^-1; 0 is cooling
    Flux(f64),
    /// POWER: kerma heating power density, W cm^-3
    Power(f64),
    /// WALL: first-wall loading, MW m^-2
    Wall(f64),
}

impl Drive {
    fn irradiates(self) -> bool {
        match self {
            Drive::Flux(value) | Drive::Power(value) | Drive::Wall(value) => value > 0.0,
        }
    }
}

struct StepRec {
    dt: String,
    drive: Drive,
    line: usize,
    flag: &'static str,
    /// element -> fraction of its atoms kept at the start of the step (PARTITION)
    partition: Option<BTreeMap<String, f64>>,
}

/// One OVER replacement, in the spec `overrides` vocabulary.
#[derive(Debug, Clone, PartialEq)]
struct OverrideRec {
    nuclide: String,
    kind: &'static str,
    mt: Option<i32>,
    value: f64,
    units: Option<&'static str>,
}

/// The deck's photon-side keywords, assembled into spec `photon` in `finish`.
#[derive(Default)]
struct PhotonRec {
    group_structure: Option<&'static str>,
    point_source_m: Option<f64>,
    bremsstrahlung: Vec<String>,
}

/// GRPCONVERT, CNVTYPE and LIMGRP, each with the report entry that `finish` settles.
#[derive(Default)]
struct RebinRec {
    grpconvert: Option<(Vec<f64>, Option<usize>)>,
    cnvtype: Option<(u8, Option<usize>)>,
    limgrp: Option<(f64, f64, Option<usize>)>,
}

struct Interp<'a> {
    ctx: &'a ImportContext,
    entries: Vec<Entry>,
    recording: bool,
    title: String,
    material: Option<MaterialDef>,
    density: Option<f64>,
    projectile: Projectile,
    ebins: Option<usize>,
    current: Drive,
    total_flux: Option<f64>,
    pending: Option<Pending>,
    steps: Vec<StepRec>,
    pending_partition: BTreeMap<String, f64>,
    partition_entries: Vec<usize>,
    wall_entries: Vec<usize>,
    power_line: Option<usize>,
    overrides: Vec<OverrideRec>,
    photon: PhotonRec,
    rebin: RebinRec,
    pathway_targets: Vec<String>,
    pathway_entries: Vec<usize>,
    a2_line: Option<usize>,
    zero_line: Option<usize>,
    iuncer: i64,
    uncert_entry: Option<usize>,
    noerror: bool,
    min_atoms: Option<f64>,
    include_stable: Option<bool>,
    dominant_top: Option<u64>,
    ended: bool,
}

fn arg_text<'c>(cmd: &'c Command, index: usize, what: &str) -> Result<&'c str, String> {
    cmd.args
        .get(index)
        .map(String::as_str)
        .ok_or_else(|| format!("line {}: {} needs {what}", cmd.line, cmd.keyword))
}

fn arg_number(cmd: &Command, index: usize, what: &str) -> Result<f64, String> {
    let raw = arg_text(cmd, index, what)?;
    parse_number(raw).ok_or_else(|| {
        format!(
            "line {}: {} {what} '{raw}' is not a number",
            cmd.line, cmd.keyword
        )
    })
}

fn arg_whole(cmd: &Command, index: usize, what: &str) -> Result<i64, String> {
    let value = arg_number(cmd, index, what)?;
    if value.fract() != 0.0 || value.abs() > 1.0e9 {
        return Err(format!(
            "line {}: {} {what} must be a whole number, got {value}",
            cmd.line, cmd.keyword
        ));
    }
    Ok(value as i64)
}

/// Dispositions of keywords with no dedicated handler.
fn simple_disposition(keyword: &str) -> Option<(Status, &'static str)> {
    use Status::{Noted, Unsupported};
    Some(match keyword {
        "*" => (Noted, "text line"),
        "CLOBBER" => (Noted, "output-file overwrite policy; ACTINV writes only the files you name"),
        "MONITOR" | "LOGLEVEL" => (Noted, "run-log verbosity"),
        "JSON" => (Noted, "ACTINV results are always JSON"),
        "PRINTLIB" | "SAVELINES" | "FULLXS" | "SPEK" | "COMPRESSXS" | "EXTRACTXS" | "LIBVERSION" => (
            Noted,
            "FISPACT-II library preparation and printing; ACTINV reads its activation library and decay data from the spec",
        ),
        "HALF" => (Noted, "half-life output column; ACTINV reports nuclide data in every result step"),
        "HAZARDS" | "CLEAR" => (
            Noted,
            "regulatory output columns; ACTINV picks no jurisdiction or dose table for you: name one in the spec radiological section (bundled:NAME tables ship for clearance and transport limits, none for ICRP dose coefficients) or use `actinv clearance`",
        ),
        "GRAPH" | "NUCGRAPH" | "TAB1" | "TAB2" | "TAB3" | "TAB4" | "NOT1" | "NOT2" | "NOT3" | "NOT4" | "CULTAB" | "NOSORT"
        | "NOCOMP" | "SPLIT" => (Noted, "output selector or file format; the result JSON is complete for every step"),
        "TOLERANCE" => (
            Noted,
            "FISPACT-II solver tolerances; ACTINV uses CRAM with the options.bmin_atoms_per_g pruning bound",
        ),
        "INDEXPATH" | "LOOKAHEAD" | "PATHRESET" | "GENERIC" => (
            Noted,
            "pathway-analysis presentation; ACTINV's pathways output is selected with options.outputs",
        ),
        "ALLDISPEN" | "ATDISPEN" => (
            Unsupported,
            "displacement-damage rates need a damage table in the spec (damage.displacement_energy_eV)",
        ),
        "COVARIANCE" | "UNCTYPE" | "ERROR" | "MCSAMPLE" | "SENSITIVITY" => (
            Unsupported,
            "FISPACT-II covariance, sampling and sensitivity controls have no spec field; use the uncertainty option",
        ),
        "CUMFYLD" | "FISYIELD" | "FISCHOOSE" | "USEFISSION" | "NOFISS" => (
            Unsupported,
            "fission treatment is set by spec.fission_yields.files, not by keyword",
        ),
        "DEPLETION" => (Unsupported, "nuclide depletion switches are not available"),
        "IRON" => (Unsupported, "fictitious-matrix iron background removal is not available"),
        "NOXRAY" | "READGG" | "READSF" | "USESPALLATION" | "USEXSEXTRA" | "XSTHRESHOLD" => (
            Unsupported,
            "library-data selection or modification keyword with no ACTINV equivalent",
        ),
        "RESULT" => (
            Unsupported,
            "pathway result selection has no spec field; the pathways section reports the targets it is given",
        ),
        "PROBTABLE" => (
            Unsupported,
            "probability-table self-shielding is spec self_shielding.table, a hash-pinned Bondarenko table the deck does not name; add the section by hand",
        ),
        "SSFGEOMETRY" | "SSFCHOOSE" | "SSFFUEL" | "SSFMASS" => (
            Unsupported,
            "geometry self-shielding is spec self_shielding.geometry (shape, length_cm, density_g_cm3 and hash-pinned ENDF resonance_files); the deck names no resonance evaluations, so the section cannot be built from it: add it by hand",
        ),
        "SSFDILUTION" => (
            Unsupported,
            "per-group dilution overrides are spec self_shielding.dilution_overrides, which needs a hash-pinned Bondarenko table the deck does not name: add the section by hand",
        ),
        _ => return None,
    })
}

/// A nuclide as a deck names it (`Fe56`, `Co58m`, and `n` for a second isomer: `Te129n`) as its
/// canonical ACTINV key with the ZA and isomer number.
fn fispact_nuclide(raw: &str) -> Result<(String, i32, i32), String> {
    let name = match raw.strip_suffix(['n', 'N']) {
        Some(stem) if stem.ends_with(|c: char| c.is_ascii_digit()) => format!("{stem}m2"),
        _ => raw.to_string(),
    };
    match material_key(&name) {
        Ok(MaterialKey::Nuclide {
            canonical,
            za,
            liso,
            ..
        }) => Ok((canonical, za, liso)),
        Ok(MaterialKey::Element(_)) => Err(format!("'{raw}' is an element, not a nuclide")),
        Err(error) => Err(format!("'{raw}': {error}")),
    }
}

/// The ENDF reaction number an `ACROSS target daughter` pair names for a neutron, from the change
/// in Z and A. Only changes one reaction produces are accepted: (n,g) 102, (n,2n) 16, (n,3n) 17,
/// (n,p) 103 and (n,a) 107. Others (Z-1, A-1 is both (n,d) and (n,np)) cannot be told apart.
fn reaction_mt(target_za: i32, daughter_za: i32) -> Result<i32, String> {
    let dz = daughter_za / 1000 - target_za / 1000;
    let da = daughter_za % 1000 - target_za % 1000;
    match (dz, da) {
        (0, 1) => Ok(102),
        (0, -1) => Ok(16),
        (0, -2) => Ok(17),
        (-1, 0) => Ok(103),
        (-2, -3) => Ok(107),
        _ => Err(format!(
            "the change dZ={dz}, dA={da} is not one reaction that an ENDF number selects unambiguously ((n,g), (n,2n), (n,3n), (n,p), (n,a))"
        )),
    }
}

/// Element/fraction pairs of a PARTITION card (fractions of the element's atoms kept), after an
/// optional leading pair count. A repeated element multiplies its fractions.
fn partition_pairs(words: &[String]) -> Result<BTreeMap<String, f64>, String> {
    let declared = words
        .first()
        .and_then(|word| parse_number(word))
        .filter(|value| value.fract() == 0.0 && *value >= 1.0);
    let body = if declared.is_some() {
        &words[1..]
    } else {
        words
    };
    if body.is_empty() || body.len() % 2 != 0 {
        return Err("expected element/fraction pairs".into());
    }
    if let Some(count) = declared {
        if count * 2.0 != body.len() as f64 {
            return Err(format!(
                "declares {count} element(s) but lists {} word(s)",
                body.len()
            ));
        }
    }
    let mut kept = BTreeMap::new();
    for pair in body.chunks(2) {
        let element = match material_key(&pair[0]) {
            Ok(MaterialKey::Element(element)) => element,
            _ => return Err(format!("'{}' is not an element symbol", pair[0])),
        };
        let fraction = parse_number(&pair[1])
            .filter(|value| (0.0..=1.0).contains(value))
            .ok_or_else(|| {
                format!(
                    "the fraction '{}' for {element} is not a number in [0, 1]",
                    pair[1]
                )
            })?;
        *kept.entry(element).or_insert(1.0) *= fraction;
    }
    Ok(kept)
}

/// Ascending boundaries of the FISPACT-II standard structure with this many groups (a neutron
/// grid, or the charged-particle 162-group grid).
fn standard_boundaries(groups: usize, neutron: bool) -> Option<Vec<f64>> {
    NAMED_STRUCTURES
        .iter()
        .filter_map(|(name, _)| GroupStructure::named(name).ok().flatten())
        .find(|structure| {
            structure.groups() == groups && (structure.name == "fispact-162") != neutron
        })
        .map(|structure| structure.boundaries_ev)
}

impl<'a> Interp<'a> {
    fn new(ctx: &'a ImportContext) -> Self {
        Interp {
            ctx,
            entries: Vec::new(),
            recording: true,
            title: String::new(),
            material: None,
            density: None,
            projectile: Projectile::Neutron,
            ebins: None,
            current: Drive::Flux(0.0),
            total_flux: None,
            pending: None,
            steps: Vec::new(),
            pending_partition: BTreeMap::new(),
            partition_entries: Vec::new(),
            wall_entries: Vec::new(),
            power_line: None,
            overrides: Vec::new(),
            photon: PhotonRec::default(),
            rebin: RebinRec::default(),
            pathway_targets: Vec::new(),
            pathway_entries: Vec::new(),
            a2_line: None,
            zero_line: None,
            iuncer: 0,
            uncert_entry: None,
            noerror: false,
            min_atoms: None,
            include_stable: None,
            dominant_top: None,
            ended: false,
        }
    }

    fn log(&mut self, cmd: &Command, status: Status, detail: impl Into<String>) -> Option<usize> {
        if !self.recording {
            return None;
        }
        let mut args = cmd.args.join(" ");
        if args.chars().count() > 80 {
            args = args.chars().take(80).collect::<String>() + "...";
        }
        self.entries.push(Entry {
            keyword: cmd.keyword.clone(),
            line: cmd.line,
            phase: cmd.phase,
            status,
            detail: detail.into(),
            args,
        });
        Some(self.entries.len() - 1)
    }

    fn run_nodes(&mut self, nodes: &[Node], record: bool) -> Result<(), String> {
        for node in nodes {
            match node {
                Node::Cmd(cmd) => {
                    self.recording = record;
                    self.apply(cmd)?;
                }
                Node::Pulse { cmd, count, body } => {
                    self.recording = record;
                    let detail = if *count == 0 {
                        "zero repetitions: the loop body is skipped".to_string()
                    } else {
                        format!("loop body expanded {count} times into explicit schedule steps")
                    };
                    self.log(cmd, Status::Mapped, detail);
                    for repeat in 0..*count {
                        // keywords inside the loop are reported once, not once per repetition
                        self.run_nodes(body, record && repeat == 0)?;
                    }
                }
            }
        }
        Ok(())
    }

    fn library_is_explicit(&self) -> bool {
        self.ctx.library.is_some()
    }

    fn apply(&mut self, cmd: &Command) -> Result<(), String> {
        if !cmd.known {
            self.log(
                cmd,
                Status::Unsupported,
                "not a FISPACT-II keyword; the importer cannot tell what it was meant to do",
            );
            return Ok(());
        }
        match cmd.keyword.as_str() {
            "FISPACT" => {
                if let Some(title) = cmd.args.first() {
                    self.title = title.clone();
                }
                self.log(cmd, Status::Mapped, "title -> spec.title");
            }
            "END" => {
                self.ended = true;
                self.log(
                    cmd,
                    Status::Noted,
                    "end of the deck; later lines are ignored, as in FISPACT-II",
                );
            }
            "MASS" => self.apply_mass(cmd)?,
            "FUEL" => self.apply_fuel(cmd)?,
            "DENSITY" => {
                let density = arg_number(cmd, 0, "density (g/cm3)")?;
                self.density = Some(density);
                self.log(
                    cmd,
                    Status::Noted,
                    "written to material.density_g_cm3 only when POWER needs it (power_W_cm3); otherwise FISPACT-II uses it for per-volume output only and it is left unset so a converted problem stays identical to a hand-written one",
                );
            }
            "DOSE" => self.apply_dose(cmd)?,
            "GROUP" => self.apply_group(cmd),
            "BREMSSTRAHLUNG" => self.apply_bremsstrahlung(cmd),
            "FLUX" => {
                let flux = arg_number(cmd, 0, "flux (n/cm2/s)")?;
                if flux < 0.0 {
                    return Err(format!("line {}: FLUX must not be negative", cmd.line));
                }
                self.current = Drive::Flux(flux);
                if flux > 0.0 && self.total_flux.is_none() {
                    self.total_flux = Some(flux);
                }
                self.log(
                    cmd,
                    Status::Mapped,
                    "schedule flux multiplier = value / spectrum.total (the first positive FLUX); 0 is cooling",
                );
            }
            "ZERO" => {
                let detail = if self.zero_line.is_some() {
                    "FISPACT-II allows ZERO once; a repeat only keeps the flux at zero"
                } else {
                    "start of cooling: flux set to zero (as in FISPACT-II); the schedule carries elapsed time"
                };
                if self.zero_line.is_none() {
                    self.zero_line = Some(cmd.line);
                }
                self.current = Drive::Flux(0.0);
                self.log(cmd, Status::Mapped, detail);
            }
            "POWER" => self.apply_power(cmd),
            "WALL" => self.apply_wall(cmd),
            "PARTITION" => self.apply_partition(cmd),
            "OVER" => self.apply_over(cmd),
            "GRPCONVERT" | "CNVTYPE" | "LIMGRP" => self.apply_rebin(cmd),
            "PATH" | "ROUTES" => self.apply_pathways(cmd),
            "ATWO" => {
                if self.a2_line.is_some() {
                    self.log(cmd, Status::Noted, "the A2 index is already requested");
                } else {
                    self.a2_line = Some(cmd.line);
                    self.log(
                        cmd,
                        Status::Approximated,
                        "radiological.table = bundled:us-49cfr173-a2, the A2 transport values of 49 CFR 173.435 (IAEA SSR-6 Rev.1, 2018), not FISPACT-II's own A2 file; the index sum(A_i/A2_i) is reported per gram, with one response per uranium absorption type (choose with radiological.responses)",
                    );
                }
            }
            "TIME" => {
                let value = arg_number(cmd, 0, "duration")?;
                if value < 0.0 {
                    return Err(format!("line {}: TIME must not be negative", cmd.line));
                }
                let unit = match cmd.args.get(1).map(String::as_str) {
                    Some("min") => "min",
                    Some("h") => "h",
                    Some("d") => "d",
                    Some("y") => "y",
                    _ => "s",
                };
                let replaced = self.pending.replace(Pending {
                    value,
                    unit,
                    line: cmd.line,
                });
                let mut detail =
                    format!("interval {value} {unit}; runs at the next ATOMS, STEP or SPECTRUM");
                if let Some(old) = replaced {
                    detail.push_str(&format!(
                        " (replaces the interval from line {}, which no output keyword ran)",
                        old.line
                    ));
                }
                self.log(cmd, Status::Mapped, detail);
            }
            "ATOMS" | "STEP" | "SPECTRUM" => self.apply_trigger(cmd)?,
            "MIND" => {
                let atoms = arg_number(cmd, 0, "atom threshold")?;
                self.min_atoms = Some(atoms);
                self.log(
                    cmd,
                    Status::Mapped,
                    "summary.min_atoms (threshold in atoms of the stated material, as in FISPACT-II)",
                );
            }
            "NOSTABLE" => {
                let late = cmd.phase == Phase::Inventory && !self.steps.is_empty();
                self.include_stable = Some(false);
                if late {
                    self.log(
                        cmd,
                        Status::Approximated,
                        "summary.include_stable=false applies to the whole run; FISPACT-II applied it from this point",
                    );
                } else {
                    self.log(cmd, Status::Mapped, "summary.include_stable=false");
                }
            }
            "SORTDOMINANT" => {
                let topxx = arg_whole(cmd, 0, "dominant-list size")?;
                let topx = if cmd.args.len() > 1 {
                    arg_whole(cmd, 1, "displayed count")?
                } else {
                    topxx.min(20)
                };
                if topx < 1 {
                    return Err(format!(
                        "line {}: SORTDOMINANT counts must be positive",
                        cmd.line
                    ));
                }
                self.dominant_top = Some(topx as u64);
                self.log(
                    cmd,
                    Status::Approximated,
                    "summary.dominant_top takes the displayed count (topx); the separate uncertainty list size (topxx) has no equivalent",
                );
            }
            "UNCERTAINTY" => {
                let first = arg_whole(cmd, 0, "level")?;
                // -1 resets the pathway defaults; the level to use then follows the three floors
                let level = if first == -1 && cmd.args.len() >= 5 {
                    arg_whole(cmd, cmd.args.len() - 1, "level")?
                } else if first == -1 {
                    0
                } else {
                    first
                };
                if !(0..=3).contains(&level) {
                    return Err(format!(
                        "line {}: UNCERTAINTY level must be 0 to 3, got {level}",
                        cmd.line
                    ));
                }
                self.iuncer = level;
                self.uncert_entry = self.log(cmd, Status::Noted, "resolved when the deck ends");
            }
            "NOERROR" => {
                self.noerror = true;
                self.log(cmd, Status::Mapped, "no uncertainty block is emitted");
            }
            "PROJECTILE" => {
                let code = arg_whole(cmd, 0, "projectile code")?;
                let projectile = match code {
                    1 => Projectile::Neutron,
                    2 => Projectile::Deuteron,
                    3 => Projectile::Proton,
                    4 => Projectile::Alpha,
                    5 => Projectile::Gamma,
                    6 => Projectile::Triton,
                    7 => Projectile::Helion,
                    other => {
                        self.log(cmd, Status::Unsupported, format!("projectile code {other}"));
                        return Err(format!(
                            "line {}: PROJECTILE {other} is not a FISPACT-II projectile code (1-7)",
                            cmd.line
                        ));
                    }
                };
                self.projectile = projectile;
                let mut detail = format!("projectile \"{}\"", projectile.name());
                if !self.library_is_explicit()
                    && matches!(projectile, Projectile::Triton | Projectile::Helion)
                {
                    detail.push_str("; no catalog library exists for it, so --library is required");
                }
                self.log(cmd, Status::Mapped, detail);
            }
            "GETXS" => {
                let mode = arg_whole(cmd, 0, "library mode")?;
                if cmd.phase == Phase::Inventory {
                    let next = self.steps.len();
                    self.log(
                        cmd,
                        Status::Unsupported,
                        format!("a mid-run cross-section or spectrum change. ACTINV expresses it as a step library override, which needs the second library's path and the spectrum in its group structure; the deck names neither. Add `library` and `spectrum` to schedule[{next}] (the next step to run) by hand; step libraries are refused beside uncertainty, kerma, power, partition, pathways and cross-section overrides"),
                    );
                } else {
                    if mode != 0 && cmd.args.len() > 1 {
                        let groups = arg_whole(cmd, 1, "group count")?;
                        if groups > 0 {
                            self.ebins = Some(groups as usize);
                        }
                    }
                    if self.library_is_explicit() {
                        self.log(
                            cmd,
                            Status::Mapped,
                            "cross sections come from spec.library (--library); ACTINV collapses them with the spectrum itself",
                        );
                    } else {
                        self.log(
                            cmd,
                            Status::Approximated,
                            "FISPACT-II's own collapsed library is not read; the default ACTINV library for the projectile is used (pass --library to choose)",
                        );
                    }
                }
            }
            "GETDECAY" => {
                if cmd.phase == Phase::Inventory {
                    self.log(
                        cmd,
                        Status::Unsupported,
                        "decay data cannot change during the run",
                    );
                } else if self.ctx.decay.is_some() {
                    self.log(
                        cmd,
                        Status::Mapped,
                        "decay data come from spec.decay (--decay)",
                    );
                } else {
                    self.log(
                        cmd,
                        Status::Approximated,
                        "FISPACT-II's own decay library is not read; ACTINV's default decay bundle is used (pass --decay to choose)",
                    );
                }
            }
            "PULSE" | "ENDPULSE" => {}
            other => {
                let (status, detail) = simple_disposition(other).unwrap_or((
                    Status::Unsupported,
                    "known FISPACT-II keyword without an importer rule",
                ));
                self.log(cmd, status, detail);
            }
        }
        Ok(())
    }

    fn apply_mass(&mut self, cmd: &Command) -> Result<(), String> {
        if cmd.phase == Phase::Inventory {
            self.log(
                cmd,
                Status::Unsupported,
                "a material defined after the first TIME cannot be represented",
            );
            return Ok(());
        }
        if self.material.is_some() {
            return Err(format!(
                "line {}: MASS and FUEL define the material once; a second definition is ambiguous",
                cmd.line
            ));
        }
        let total_kg = arg_number(cmd, 0, "total mass (kg)")?;
        if total_kg <= 0.0 {
            return Err(format!(
                "line {}: MASS total mass must be positive",
                cmd.line
            ));
        }
        let count = arg_whole(cmd, 1, "element count")? as usize;
        let mut elements: BTreeMap<String, f64> = BTreeMap::new();
        let mut percent_total = 0.0;
        for pair in 0..count {
            let symbol = arg_text(cmd, 2 + 2 * pair, "an element symbol")?;
            let percent = arg_number(cmd, 3 + 2 * pair, "an element percentage")?;
            if percent < 0.0 {
                return Err(format!(
                    "line {}: MASS percentage for {symbol} is negative",
                    cmd.line
                ));
            }
            let element = match material_key(symbol) {
                Ok(MaterialKey::Element(element)) => element,
                Ok(_) => {
                    return Err(format!(
                        "line {}: MASS takes element symbols, got nuclide '{symbol}'; use FUEL for isotopic material",
                        cmd.line
                    ))
                }
                Err(error) => return Err(format!("line {}: MASS element '{symbol}': {error}", cmd.line)),
            };
            *elements.entry(element).or_insert(0.0) += percent;
            percent_total += percent;
        }
        let mut detail = format!(
            "material.mass_g = {} g, basis wt_percent, {} element(s)",
            total_kg * 1000.0,
            elements.len()
        );
        if (percent_total - 100.0).abs() > 1.0e-9 {
            detail.push_str(&format!(
                "; percentages sum to {percent_total}, not 100, so only that fraction of the mass is material (the same reading as FISPACT-II)"
            ));
        }
        self.material = Some(MaterialDef::Mass {
            total_g: total_kg * 1000.0,
            elements,
        });
        self.log(cmd, Status::Mapped, detail);
        Ok(())
    }

    fn apply_fuel(&mut self, cmd: &Command) -> Result<(), String> {
        if cmd.phase == Phase::Inventory {
            self.log(
                cmd,
                Status::Unsupported,
                "a material defined after the first TIME cannot be represented",
            );
            return Ok(());
        }
        if self.material.is_some() {
            return Err(format!(
                "line {}: MASS and FUEL define the material once; a second definition is ambiguous",
                cmd.line
            ));
        }
        let count = arg_whole(cmd, 0, "nuclide count")? as usize;
        let mut atoms: BTreeMap<String, (f64, i32)> = BTreeMap::new();
        for pair in 0..count {
            let raw = arg_text(cmd, 1 + 2 * pair, "a nuclide name")?;
            let amount = arg_number(cmd, 2 + 2 * pair, "an atom count")?;
            if amount < 0.0 {
                return Err(format!(
                    "line {}: FUEL atom count for {raw} is negative",
                    cmd.line
                ));
            }
            // FISPACT-II writes a second isomer as `n`: Te129n is ACTINV's Te129m2
            let name = match raw.strip_suffix(['n', 'N']) {
                Some(stem) if stem.ends_with(|c: char| c.is_ascii_digit()) => format!("{stem}m2"),
                _ => raw.to_string(),
            };
            match material_key(&name) {
                Ok(MaterialKey::Nuclide { canonical, za, .. }) => {
                    let entry = atoms.entry(canonical).or_insert((0.0, za % 1000));
                    entry.0 += amount;
                }
                Ok(MaterialKey::Element(_)) => {
                    return Err(format!(
                        "line {}: FUEL takes nuclides such as Li6, got element '{raw}'; use MASS for natural elements",
                        cmd.line
                    ))
                }
                Err(error) => return Err(format!("line {}: FUEL nuclide '{raw}': {error}", cmd.line)),
            }
        }
        // the mass number stands in for the isotopic mass: atoms/g would change by under 0.1% per
        // nuclide, but the atom totals themselves are exact (atoms_per_g x mass_g = atoms)
        let total_g: f64 = atoms.values().map(|(n, a)| n * f64::from(*a)).sum::<f64>() / NA;
        if total_g <= 0.0 {
            return Err(format!("line {}: FUEL lists no atoms", cmd.line));
        }
        let atoms_per_g = atoms
            .into_iter()
            .map(|(name, (n, _))| (name, n / total_g))
            .collect();
        self.material = Some(MaterialDef::Fuel {
            total_g,
            atoms_per_g,
        });
        self.log(
            cmd,
            Status::Approximated,
            format!(
                "material basis atoms_per_g with mass_g = {total_g} g from mass numbers; the atom counts are exact, the stated mass is within about 0.1%"
            ),
        );
        Ok(())
    }

    fn apply_trigger(&mut self, cmd: &Command) -> Result<(), String> {
        let flag = match cmd.keyword.as_str() {
            "ATOMS" => "atoms",
            "STEP" => "step",
            _ => "spectrum",
        };
        if cmd.phase != Phase::Inventory {
            self.log(
                cmd,
                Status::Noted,
                "initial-inventory output; ACTINV reports results after each schedule step",
            );
            return Ok(());
        }
        let Some(pending) = self.pending.take() else {
            self.log(
                cmd,
                Status::Noted,
                "no TIME interval is pending, so no step runs (FISPACT-II resets the interval after each output)",
            );
            return Ok(());
        };
        if self.steps.len() >= MAX_STEPS {
            return Err(format!(
                "line {}: the schedule exceeds {MAX_STEPS} steps after PULSE expansion",
                cmd.line
            ));
        }
        let partition = std::mem::take(&mut self.pending_partition);
        self.partition_entries.clear();
        self.steps.push(StepRec {
            dt: format!("{:?} {}", pending.value, pending.unit),
            drive: self.current,
            line: pending.line,
            flag,
            partition: (!partition.is_empty()).then_some(partition),
        });
        let detail = if flag == "atoms" {
            "runs the pending TIME interval as one schedule step".to_string()
        } else {
            format!("runs the pending TIME interval as one schedule step; the {flag} output form is not reproduced (every step carries full results)")
        };
        self.log(cmd, Status::Mapped, detail);
        Ok(())
    }

    /// POWER: the steps that follow are driven by a heating power, not a flux.
    fn apply_power(&mut self, cmd: &Command) {
        let Some(power) = cmd.args.first().and_then(|word| parse_number(word)) else {
            self.log(cmd, Status::Unsupported, "POWER needs a power density");
            return;
        };
        if power < 0.0 {
            self.log(cmd, Status::Unsupported, "POWER must not be negative");
            return;
        }
        if power == 0.0 {
            self.current = Drive::Flux(0.0);
            self.log(cmd, Status::Mapped, "zero power: cooling, as FLUX 0");
            return;
        }
        self.current = Drive::Power(power);
        if self.power_line.is_none() {
            self.power_line = Some(cmd.line);
        }
        // POWER p n mt_1 .. mt_n: p in W/cc, normalized on the kerma of the listed MTs
        let mts: Vec<&str> = cmd.args.iter().skip(2).map(String::as_str).collect();
        let mut detail = format!(
            "steps that follow take power_W_cm3 = {power} (FISPACT-II's W/cc) with material.density_g_cm3 from DENSITY; the flux amplitude is solved from kerma heating, which needs a hash-pinned kerma.table that no deck provides (see required_additions)"
        );
        if mts != ["301"] {
            detail.push_str(&format!(
                "; the deck normalizes on MT list '{}', ACTINV on the table's total kerma (MT=301)",
                cmd.args[1..].join(" ")
            ));
        }
        self.log(cmd, Status::Approximated, detail);
    }

    /// WALL: the steps that follow are driven by a first-wall loading in MW m^-2.
    fn apply_wall(&mut self, cmd: &Command) {
        let Some(wall) = cmd.args.first().and_then(|word| parse_number(word)) else {
            self.log(cmd, Status::Unsupported, "WALL needs a loading in MW/m2");
            return;
        };
        if wall < 0.0 {
            self.log(cmd, Status::Unsupported, "WALL must not be negative");
            return;
        }
        if wall == 0.0 {
            self.current = Drive::Flux(0.0);
            self.log(cmd, Status::Mapped, "zero loading: cooling, as FLUX 0");
            return;
        }
        self.current = Drive::Wall(wall);
        let index = self.log(
            cmd,
            Status::Mapped,
            format!("steps that follow take wall_loading_MW_m2 = {wall}"),
        );
        self.wall_entries.extend(index);
    }

    /// PARTITION: an instantaneous elemental split, applied at the start of the next step.
    fn apply_partition(&mut self, cmd: &Command) {
        match partition_pairs(&cmd.args) {
            Ok(kept) => {
                let shown: Vec<String> = kept
                    .iter()
                    .map(|(element, fraction)| format!("{element} {fraction}"))
                    .collect();
                for (element, fraction) in kept {
                    *self.pending_partition.entry(element).or_insert(1.0) *= fraction;
                }
                let index = self.log(
                    cmd,
                    Status::Mapped,
                    format!(
                        "schedule partition (fraction of each element's atoms kept: {}) at the start of the next step; the removed atoms go to the removal sink",
                        shown.join(", ")
                    ),
                );
                self.partition_entries.extend(index);
            }
            Err(reason) => {
                self.log(cmd, Status::Unsupported, format!("PARTITION: {reason}"));
            }
        }
    }

    /// One `overrides` item, refusing a second, different value for the same replacement.
    fn add_override(&mut self, record: OverrideRec) -> Result<(), String> {
        let known = self
            .overrides
            .iter()
            .find(|known| {
                known.nuclide == record.nuclide
                    && known.kind == record.kind
                    && known.mt == record.mt
            })
            .cloned();
        match known {
            Some(known) if known == record => Ok(()),
            Some(known) => Err(format!(
                "{} of {} was already set to {}",
                record.kind, record.nuclide, known.value
            )),
            None => {
                self.overrides.push(record);
                Ok(())
            }
        }
    }

    /// `ACROSS daughter value`: the collapsed cross section of one reaction of the OVER target.
    fn over_cross_section(
        &mut self,
        target: Option<&(String, i32)>,
        daughter: &str,
        value: f64,
    ) -> (Status, String) {
        let fail = |reason: String| (Status::Unsupported, reason);
        let Some((nuclide, target_za)) = target else {
            return fail("no OVER target nuclide precedes it".into());
        };
        if !self.projectile.is_neutron() {
            return fail(
                "the daughter-to-ENDF-reaction mapping is defined for neutron projectiles only"
                    .into(),
            );
        }
        if value < 0.0 {
            return fail("a cross section must not be negative".into());
        }
        let (daughter_name, daughter_za, daughter_liso) = match fispact_nuclide(daughter) {
            Ok(parsed) => parsed,
            Err(reason) => return fail(format!("daughter {reason}")),
        };
        if daughter_liso > 0 {
            return fail(format!(
                "daughter {daughter_name} is an isomeric state; ACTINV overrides replace a whole ENDF reaction row, so one isomer's branch cannot be set"
            ));
        }
        let mt = match reaction_mt(*target_za, daughter_za) {
            Ok(mt) => mt,
            Err(reason) => return fail(reason),
        };
        let record = OverrideRec {
            nuclide: nuclide.clone(),
            kind: "cross_section",
            mt: Some(mt),
            value,
            units: None,
        };
        match self.add_override(record) {
            Ok(()) => (
                Status::Approximated,
                format!(
                    "overrides: cross_section {nuclide} MT {mt} = {value} b (daughter {daughter_name}); FISPACT-II sets the channel to that daughter, ACTINV sets the collapsed MT row with its product branching kept"
                ),
            ),
            Err(reason) => fail(reason),
        }
    }

    /// `ALAM value [unit]`: the half-life of the OVER target.
    fn over_half_life(
        &mut self,
        target: Option<&(String, i32)>,
        value: f64,
        units: Option<&'static str>,
    ) -> (Status, String) {
        let Some((nuclide, _)) = target else {
            return (
                Status::Unsupported,
                "no OVER target nuclide precedes it".into(),
            );
        };
        if value <= 0.0 {
            return (Status::Unsupported, "a half-life must be positive".into());
        }
        let record = OverrideRec {
            nuclide: nuclide.clone(),
            kind: "half_life",
            mt: None,
            value,
            units,
        };
        match self.add_override(record) {
            Ok(()) => (
                Status::Mapped,
                format!(
                    "overrides: half_life {nuclide} = {value} {}",
                    units.unwrap_or("s")
                ),
            ),
            Err(reason) => (Status::Unsupported, reason),
        }
    }

    /// OVER: named per-nuclide replacements. Each sub-keyword is reported under its own name.
    fn apply_over(&mut self, cmd: &Command) {
        let words = &cmd.args;
        let block = self.log(cmd, Status::Mapped, "resolved below");
        let mut target: Option<(String, i32)> = None;
        let (mut mapped, mut approximated, mut unsupported) = (0usize, 0usize, 0usize);
        let mut stray: Vec<&str> = Vec::new();
        let mut index = 0;
        while index < words.len() {
            let word = words[index].as_str();
            index += 1;
            let sub = word.to_ascii_uppercase();
            let (shown, outcome): (String, (Status, String)) = match sub.as_str() {
                "ACROSS" | "ADCROSS" => {
                    let daughter = words.get(index).map(String::as_str);
                    let value = words.get(index + 1).and_then(|raw| parse_number(raw));
                    match (daughter, value) {
                        (Some(daughter), Some(value)) => {
                            index += 2;
                            let shown = format!("{daughter} {value}");
                            if sub == "ACROSS" {
                                (
                                    shown,
                                    self.over_cross_section(target.as_ref(), daughter, value),
                                )
                            } else {
                                (shown, (Status::Unsupported, "ACTINV's cross_section_uncertainty takes a relative standard uncertainty, FISPACT-II's value is an error factor, and the conversion is not defined by the spec; set overrides cross_section_uncertainty by hand".into()))
                            }
                        }
                        _ => (
                            String::new(),
                            (
                                Status::Unsupported,
                                "needs a daughter nuclide and a value".into(),
                            ),
                        ),
                    }
                }
                "ALAM" | "ADLAM" => {
                    let Some(value) = words.get(index).and_then(|raw| parse_number(raw)) else {
                        self.sub_entry(
                            cmd,
                            &sub,
                            String::new(),
                            Status::Unsupported,
                            "needs a value".into(),
                        );
                        unsupported += 1;
                        continue;
                    };
                    index += 1;
                    let units = words.get(index).and_then(|raw| time_unit(raw));
                    if units.is_some() {
                        index += 1;
                    }
                    let shown = format!("{value} {}", units.unwrap_or("s"));
                    if sub == "ALAM" {
                        (shown, self.over_half_life(target.as_ref(), value, units))
                    } else {
                        (shown, (Status::Unsupported, "ACTINV's half_life_uncertainty takes a relative standard uncertainty, FISPACT-II's value is an error factor, and the conversion is not defined by the spec; set overrides half_life_uncertainty by hand".into()))
                    }
                }
                _ => {
                    match fispact_nuclide(word) {
                        Ok((canonical, za, _)) => target = Some((canonical, za)),
                        Err(_) => stray.push(word),
                    }
                    continue;
                }
            };
            match outcome.0 {
                Status::Mapped => mapped += 1,
                Status::Unsupported => unsupported += 1,
                _ => approximated += 1,
            }
            self.sub_entry(cmd, &sub, shown, outcome.0, outcome.1);
        }
        let total = mapped + approximated + unsupported;
        let mut detail = format!(
            "{total} sub-keyword(s): {mapped} mapped, {approximated} approximated, {unsupported} unsupported (each is reported under its own name)"
        );
        let mut status = if total == 0 || unsupported == total {
            Status::Unsupported
        } else if approximated + unsupported > 0 {
            Status::Approximated
        } else {
            Status::Mapped
        };
        if total == 0 {
            detail = "no ACROSS, ALAM, ADCROSS or ADLAM sub-keyword was read".into();
        }
        if !stray.is_empty() {
            detail.push_str(&format!("; not interpreted: {}", stray.join(" ")));
            if status == Status::Mapped {
                status = Status::Approximated;
            }
        }
        self.set_entry(block, status, detail);
    }

    /// A report entry for one sub-keyword of a multi-part keyword, on the parent's line.
    fn sub_entry(
        &mut self,
        parent: &Command,
        keyword: &str,
        args: String,
        status: Status,
        detail: String,
    ) {
        let sub = Command {
            keyword: keyword.to_string(),
            args: if args.is_empty() {
                Vec::new()
            } else {
                vec![args]
            },
            ..parent.clone()
        };
        self.log(&sub, status, detail);
    }

    /// GRPCONVERT, CNVTYPE and LIMGRP are settled together in `finish`, once the spectrum is known.
    fn apply_rebin(&mut self, cmd: &Command) {
        if cmd.phase == Phase::Inventory {
            self.log(
                cmd,
                Status::Unsupported,
                "a spectrum conversion after the first TIME cannot be represented",
            );
            return;
        }
        let numbers: Vec<f64> = cmd
            .args
            .iter()
            .filter_map(|word| parse_number(word))
            .collect();
        match cmd.keyword.as_str() {
            "GRPCONVERT" => {
                let index = self.log(cmd, Status::Noted, "resolved when the deck ends");
                self.rebin.grpconvert = Some((numbers, index));
            }
            "CNVTYPE" => match numbers.first() {
                Some(code) if *code == 0.0 || *code == 1.0 => {
                    let index = self.log(cmd, Status::Noted, "resolved when the deck ends");
                    self.rebin.cnvtype = Some((*code as u8, index));
                }
                _ => {
                    self.log(
                        cmd,
                        Status::Unsupported,
                        "CNVTYPE is 0 (equal lethargy) or 1 (equal energy)",
                    );
                }
            },
            _ => match numbers.as_slice() {
                [low, high, ..] if *low > 0.0 && low < high => {
                    let index = self.log(cmd, Status::Noted, "resolved when the deck ends");
                    self.rebin.limgrp = Some((*low, *high, index));
                }
                _ => {
                    self.log(
                        cmd,
                        Status::Unsupported,
                        "LIMGRP needs a lower and an upper energy limit, 0 < low < high",
                    );
                }
            },
        }
    }

    /// PATH and ROUTES: nuclides named become `pathways.targets`.
    fn apply_pathways(&mut self, cmd: &Command) {
        let mut targets: Vec<String> = Vec::new();
        let mut unread: Vec<&str> = Vec::new();
        for word in &cmd.args {
            match fispact_nuclide(word) {
                Ok((canonical, ..)) => {
                    if !targets.contains(&canonical) {
                        targets.push(canonical);
                    }
                }
                Err(_) => unread.push(word),
            }
        }
        // a leading whole number that matches the nuclide count is the count, not a setting
        if let Some(first) = unread.first() {
            if parse_number(first).is_some_and(|count| count == targets.len() as f64) {
                unread.remove(0);
            }
        }
        for target in targets {
            if !self.pathway_targets.contains(&target) {
                self.pathway_targets.push(target);
            }
        }
        let mut detail = String::from(
            "pathways section with the nuclides named as targets (none named selects the dominant ones); ACTINV's route search thresholds keep their defaults",
        );
        if !unread.is_empty() {
            detail.push_str(&format!(
                "; the numeric or other argument(s) '{}' are not interpreted",
                unread.join(" ")
            ));
        }
        let index = self.log(cmd, Status::Approximated, detail);
        self.pathway_entries.extend(index);
    }

    /// DOSE 1 (the default contact dose) or DOSE 2 distance (point-source dose).
    fn apply_dose(&mut self, cmd: &Command) -> Result<(), String> {
        let mode = if cmd.args.is_empty() {
            1
        } else {
            arg_whole(cmd, 0, "geometry")?
        };
        match mode {
            1 => {
                self.photon.point_source_m = None;
                self.log(
                    cmd,
                    Status::Noted,
                    "semi-infinite slab contact dose is the default",
                );
            }
            2 => match cmd.args.get(1).and_then(|word| parse_number(word)) {
                Some(distance) if distance > 0.0 => {
                    let used = distance.max(POINT_SOURCE_MIN_DISTANCE_M);
                    self.photon.point_source_m = Some(used);
                    let late = cmd.phase == Phase::Inventory && !self.steps.is_empty();
                    if used > distance {
                        self.log(
                            cmd,
                            Status::Approximated,
                            format!("photon.point_source.distance_m = {used}: FISPACT-II raises a distance below {POINT_SOURCE_MIN_DISTANCE_M} m to that minimum, and so does this importer (ACTINV itself rejects it)"),
                        );
                    } else if late {
                        self.log(
                            cmd,
                            Status::Approximated,
                            format!("photon.point_source.distance_m = {used} applies to the whole run; FISPACT-II applied it from this point"),
                        );
                    } else {
                        self.log(
                            cmd,
                            Status::Mapped,
                            format!("photon.point_source.distance_m = {used}; the dose values are null unless photon.response (with an air_mass_attenuation curve) is added"),
                        );
                    }
                }
                _ => {
                    self.log(
                        cmd,
                        Status::Unsupported,
                        "DOSE 2 needs a positive source-to-detector distance in metres",
                    );
                }
            },
            other => {
                self.log(
                    cmd,
                    Status::Unsupported,
                    format!("dose geometry {other} has no ACTINV equivalent"),
                );
            }
        }
        Ok(())
    }

    /// GROUP 0 (24 groups, the default) or GROUP 1 (the Steiner 22-group structure).
    fn apply_group(&mut self, cmd: &Command) {
        let structure = match cmd.args.first().and_then(|word| parse_number(word)) {
            Some(0.0) => Some("fispact-24"),
            Some(1.0) => Some("steiner-22"),
            _ => None,
        };
        match structure {
            Some(name) => {
                self.photon.group_structure = Some(name);
                self.log(
                    cmd,
                    Status::Mapped,
                    format!("photon.group_structure = \"{name}\""),
                );
            }
            None => {
                self.log(
                    cmd,
                    Status::Unsupported,
                    "only GROUP 0 (24 groups) and GROUP 1 (Steiner 22 groups) have a photon.group_structure; set group_boundaries_eV for another structure",
                );
            }
        }
    }

    /// BREMSSTRAHLUNG [count] nuclide...: `photon.bremsstrahlung.nuclides`.
    fn apply_bremsstrahlung(&mut self, cmd: &Command) {
        let declared = cmd
            .args
            .first()
            .and_then(|word| parse_number(word))
            .filter(|value| value.fract() == 0.0 && *value >= 1.0);
        let words = if declared.is_some() {
            &cmd.args[1..]
        } else {
            &cmd.args[..]
        };
        let mut nuclides: Vec<String> = Vec::new();
        for word in words {
            match fispact_nuclide(word) {
                Ok((canonical, ..)) => {
                    if !nuclides.contains(&canonical) {
                        nuclides.push(canonical);
                    }
                }
                Err(reason) => {
                    self.log(
                        cmd,
                        Status::Unsupported,
                        format!("BREMSSTRAHLUNG lists a non-nuclide: {reason}"),
                    );
                    return;
                }
            }
        }
        if nuclides.is_empty() {
            self.log(
                cmd,
                Status::Unsupported,
                "BREMSSTRAHLUNG names no nuclides; ACTINV evaluates exactly the nuclides listed and has no default candidate list",
            );
            return;
        }
        if declared.is_some_and(|count| count != words.len() as f64) {
            self.log(
                cmd,
                Status::Unsupported,
                format!(
                    "BREMSSTRAHLUNG declares {} nuclide(s) but lists {}",
                    declared.unwrap_or(0.0),
                    words.len()
                ),
            );
            return;
        }
        for nuclide in &nuclides {
            if !self.photon.bremsstrahlung.contains(nuclide) {
                self.photon.bremsstrahlung.push(nuclide.clone());
            }
        }
        self.log(
            cmd,
            Status::Approximated,
            format!(
                "photon.bremsstrahlung.nuclides = [{}]; ACTINV reports the bremsstrahlung photons and their dose in a separate photon_source.bremsstrahlung block and does not add them to the decay-photon dose",
                nuclides.join(", ")
            ),
        );
    }

    // -----------------------------------------------------------------------------------------

    fn default_library(&self) -> Option<&'static str> {
        match self.projectile {
            Projectile::Neutron => Some("catalog:tendl-2025-neutron-709g"),
            Projectile::Proton => Some("catalog:tendl-2025-proton-162g"),
            Projectile::Deuteron => Some("catalog:tendl-2025-deuteron-162g"),
            Projectile::Alpha => Some("catalog:tendl-2025-alpha-162g"),
            // No catalog library exists for gamma, triton or helion: the caller passes --library.
            Projectile::Gamma | Projectile::Triton | Projectile::Helion => None,
        }
    }

    fn set_entry(&mut self, index: Option<usize>, status: Status, detail: String) {
        if let Some(entry) = index.and_then(|index| self.entries.get_mut(index)) {
            entry.status = status;
            entry.detail = detail;
        }
    }

    /// Final status of GRPCONVERT, CNVTYPE and LIMGRP, once the spectrum is known.
    fn settle_rebin_entries(
        &mut self,
        emitted: bool,
        source_groups: Option<usize>,
        groups: usize,
        convention: &str,
    ) {
        if let Some((numbers, index)) = self.rebin.grpconvert.clone() {
            let mut detail = match source_groups {
                Some(count) if emitted && count != groups => format!(
                    "spectrum.rebin: the {count}-group spectrum (a FISPACT-II standard structure, its boundaries taken from the vendored ebins file) is converted to the library's {groups} groups inside the run"
                ),
                Some(count) if count == groups => format!(
                    "the fluxes file is already in the library's {groups}-group structure: nothing to convert"
                ),
                _ => "no fluxes file was read, so there is no spectrum to convert".to_string(),
            };
            if !numbers.is_empty() {
                detail.push_str("; the group counts come from the fluxes file, not from the keyword's arguments");
            }
            let converted = emitted && source_groups.is_some_and(|count| count != groups);
            let status = if converted {
                Status::Mapped
            } else {
                Status::Noted
            };
            self.set_entry(index, status, detail);
        }
        if let Some((_, index)) = self.rebin.cnvtype {
            let (status, detail) = if emitted {
                (
                    Status::Mapped,
                    format!("spectrum.rebin.convention = \"{convention}\""),
                )
            } else {
                (
                    Status::Noted,
                    "no spectrum conversion runs, so the convention has no effect".to_string(),
                )
            };
            self.set_entry(index, status, detail);
        }
        if let Some((low, high, index)) = self.rebin.limgrp {
            let (status, detail) = if emitted {
                (
                    Status::Approximated,
                    format!("spectrum.rebin.emin_eV = {low}, emax_eV = {high}: the two limits are read as energies in eV, lower then upper"),
                )
            } else {
                (
                    Status::Noted,
                    "no fluxes file was read, so there is no spectrum to limit".to_string(),
                )
            };
            self.set_entry(index, status, detail);
        }
    }

    /// Final status of WALL, once the spectrum file's wall loading is known.
    fn settle_wall_entries(&mut self, has_wall: bool, reference: Option<f64>) {
        if !has_wall {
            return;
        }
        for index in std::mem::take(&mut self.wall_entries) {
            let (status, detail) = match reference {
                Some(reference) => (
                    Status::Mapped,
                    format!("schedule wall_loading_MW_m2; the step multiplier is wall / {reference}, the first-wall loading of the fluxes file (spectrum.wall_loading_MW_m2), as in FISPACT-II section 4.91"),
                ),
                None => (
                    Status::Approximated,
                    "the fluxes file records no positive first-wall loading, so ACTINV applies its 4.44e13 n/cm2/s per MW/m2 (14 MeV current) equivalence with the spectrum's shape".to_string(),
                ),
            };
            self.set_entry(Some(index), status, detail);
        }
    }

    fn finish(mut self, deck_ended: bool) -> Result<Conversion, String> {
        if !deck_ended && !self.ended {
            self.entries.push(Entry {
                keyword: "END".into(),
                line: 0,
                phase: Phase::Inventory,
                status: Status::Noted,
                detail: "the deck has no END keyword; the whole file was read".into(),
                args: String::new(),
            });
        }
        if let Some(pending) = self.pending.take() {
            self.entries.push(Entry {
                keyword: "TIME".into(),
                line: pending.line,
                phase: Phase::Inventory,
                status: Status::Noted,
                detail: "this interval never ran: no ATOMS, STEP or SPECTRUM follows it".into(),
                args: String::new(),
            });
        }
        let material = self
            .material
            .take()
            .ok_or("the deck defines no material: MASS or FUEL is required")?;
        if self.steps.is_empty() {
            return Err("the deck runs no TIME step (a library-preparation-only deck has no inventory problem)".into());
        }
        let projectile = self.projectile;

        // a PARTITION that no step follows has nothing to attach to
        if !self.pending_partition.is_empty() {
            for index in std::mem::take(&mut self.partition_entries) {
                self.set_entry(
                    Some(index),
                    Status::Unsupported,
                    "no schedule step follows it: ACTINV applies a partition at the start of a step"
                        .into(),
                );
            }
        }

        // spectrum
        let source_groups = self.ctx.flux.as_ref().map(|flux| flux.values.len());
        let (structure, groups) = match &self.ctx.flux {
            Some(flux) => match flux.values.len() {
                709 => ("fispact-709", 709),
                162 => ("fispact-162", 162),
                _ if self.rebin.grpconvert.is_some() && projectile.is_neutron() => {
                    ("fispact-709", 709)
                }
                _ if self.rebin.grpconvert.is_some() => ("fispact-162", 162),
                other => {
                    return Err(format!(
                        "{} has {other} groups; ACTINV reads 709-group (neutron) or 162-group (charged-particle) spectra, so convert the spectrum first (or add GRPCONVERT to the deck for a FISPACT-II standard structure)",
                        flux.path
                    ))
                }
            },
            None if projectile.is_neutron() => ("fispact-709", 709),
            None => ("fispact-162", 162),
        };
        if let Some(ebins) = self.ebins {
            if ebins != groups && Some(ebins) != source_groups {
                return Err(format!(
                    "GETXS declares {ebins} energy groups but the flux has {groups}; supply a matching FLUXES file"
                ));
            }
        }

        // GRPCONVERT and LIMGRP: the supplied spectrum is converted onto the library structure
        // when its groups differ from it or its range is limited
        let converting = self.rebin.limgrp.is_some()
            || (self.rebin.grpconvert.is_some() && source_groups.is_some_and(|n| n != groups));
        let source_boundaries = match source_groups {
            Some(count) if converting => Some(
                standard_boundaries(count, projectile.is_neutron()).ok_or_else(|| {
                    format!(
                        "GRPCONVERT: no FISPACT-II standard group structure has {count} groups, so the boundaries of the supplied spectrum are unknown; give the spectrum in 709 (neutron) or 162 (charged-particle) groups"
                    )
                })?,
            ),
            _ => None,
        };
        let convention = match self.rebin.cnvtype {
            Some((1, _)) => "equal_energy",
            _ => "equal_lethargy",
        };

        // WALL steps scale the spectrum file's own flux by wall / (the file's wall loading)
        let has_wall = self
            .steps
            .iter()
            .any(|step| matches!(step.drive, Drive::Wall(_)));
        let wall_reference = self
            .ctx
            .flux
            .as_ref()
            .map(|flux| flux.wall_loading_mw_m2)
            .filter(|wall| wall.is_finite() && *wall > 0.0);
        let total = if has_wall {
            self.ctx
                .flux
                .as_ref()
                .map(|flux| flux.values.iter().sum::<f64>())
                .filter(|sum| *sum > 0.0)
        } else {
            self.total_flux
        };

        let mut spectrum = Map::new();
        spectrum.insert("structure".into(), json!(structure));
        match &self.ctx.flux {
            Some(flux) => {
                spectrum.insert("flux_per_group".into(), json!(flux.values));
                spectrum.insert("descending".into(), json!(true));
            }
            None => {
                if self.steps.iter().any(|step| step.drive.irradiates()) {
                    return Err(
                        "the deck irradiates (FLUX, POWER or WALL) but no FLUXES file was found: pass --fluxes FILE, or --files FILES with a `fluxes` entry"
                            .into(),
                    );
                }
                spectrum.insert("flux_per_group".into(), json!(vec![0.0; groups]));
            }
        }
        if let Some(total) = total {
            spectrum.insert("total".into(), json!(total));
        }
        if let (true, Some(reference)) = (has_wall, wall_reference) {
            spectrum.insert("wall_loading_MW_m2".into(), json!(reference));
        }
        let mut rebin_report = Value::Null;
        if let Some(boundaries) = &source_boundaries {
            let mut rebin = Map::new();
            rebin.insert("convention".into(), json!(convention));
            if let Some((low, high, _)) = self.rebin.limgrp {
                rebin.insert("emin_eV".into(), json!(low));
                rebin.insert("emax_eV".into(), json!(high));
            }
            spectrum.insert("boundaries_eV".into(), json!(boundaries));
            spectrum.insert("rebin".into(), Value::Object(rebin));
            rebin_report = json!({
                "source_groups": source_groups,
                "destination_structure": structure,
                "convention": convention,
            });
        }
        self.settle_rebin_entries(
            source_boundaries.is_some(),
            source_groups,
            groups,
            convention,
        );
        self.settle_wall_entries(has_wall, wall_reference);

        // schedule
        let schedule: Vec<Value> = self
            .steps
            .iter()
            .map(|step| {
                let mut item = Map::new();
                item.insert("dt".into(), json!(step.dt));
                match step.drive {
                    Drive::Flux(flux) => {
                        let multiplier = match total {
                            Some(total) if flux > 0.0 => flux / total,
                            _ => 0.0,
                        };
                        item.insert("flux".into(), json!(multiplier));
                    }
                    Drive::Power(power) => {
                        item.insert("power_W_cm3".into(), json!(power));
                    }
                    Drive::Wall(wall) => {
                        item.insert("wall_loading_MW_m2".into(), json!(wall));
                    }
                }
                if let Some(partition) = &step.partition {
                    item.insert("partition".into(), json!(partition));
                }
                Value::Object(item)
            })
            .collect();
        let schedule_map: Vec<Value> = self
            .steps
            .iter()
            .enumerate()
            .map(|(index, step)| {
                let kind = if step.drive.irradiates() {
                    "irradiation"
                } else {
                    "cooling"
                };
                let mut item = json!({
                    "step": index + 1,
                    "line": step.line,
                    "dt": step.dt,
                    "kind": kind,
                    "flux_cm2_s": Value::Null,
                    "output": step.flag,
                });
                match step.drive {
                    Drive::Flux(flux) => item["flux_cm2_s"] = json!(flux),
                    Drive::Power(power) => item["power_W_cm3"] = json!(power),
                    Drive::Wall(wall) => item["wall_loading_MW_m2"] = json!(wall),
                }
                item
            })
            .collect();

        // library and decay
        let defaulted_library = self.ctx.library.is_none();
        let library = match (&self.ctx.library, self.default_library()) {
            (Some(path), _) => path.clone(),
            (None, Some(default)) => default.to_string(),
            (None, None) => {
                return Err(format!(
                    "no default activation library exists for projectile '{}': pass --library",
                    projectile.name()
                ))
            }
        };
        let decay = match &self.ctx.decay {
            Some(primary) => json!({"primary": primary}),
            None => json!({
                "primary": "catalog:endfb-viii-0-decay",
                "fallback": "catalog:jeff-3-3-decay",
            }),
        };

        // uncertainty
        let has_power = self
            .steps
            .iter()
            .any(|step| matches!(step.drive, Drive::Power(_)));
        let has_partition = self.steps.iter().any(|step| step.partition.is_some());
        let uncertainty_blocker = if has_power {
            Some("power-driven schedule steps")
        } else if self
            .overrides
            .iter()
            .any(|item| item.kind.starts_with("cross_section"))
        {
            Some("cross-section overrides (the MF=33 collapse is anchored to the library value)")
        } else {
            None
        };
        let mut uncertainty = None;
        let uncert_entry = self.uncert_entry;
        if self.iuncer > 0 {
            if self.noerror {
                self.set_entry(
                    uncert_entry,
                    Status::Noted,
                    "NOERROR switches uncertainty off, so no uncertainty block is emitted".into(),
                );
            } else if !projectile.is_neutron() {
                self.set_entry(
                    uncert_entry,
                    Status::Unsupported,
                    "MF=33 uncertainty is available for neutron activation only".into(),
                );
            } else if self.iuncer == 3 {
                self.set_entry(
                    uncert_entry,
                    Status::Approximated,
                    "pathway output only: no uncertainty band is requested; ACTINV's pathways output is selected with options.outputs".into(),
                );
            } else if let Some(blocker) = uncertainty_blocker {
                self.set_entry(
                    uncert_entry,
                    Status::Unsupported,
                    format!("ACTINV refuses uncertainty beside {blocker}, so no uncertainty block is emitted"),
                );
            } else {
                let covariance = match &self.ctx.covariance {
                    Some(reference) => Some((reference.clone(), true)),
                    None if defaulted_library => {
                        let catalog = crate::embedded_catalog()?;
                        let artifact = catalog.artifact(DEFAULT_COVARIANCE)?;
                        Some((
                            CovarianceRef {
                                path: format!("catalog:{DEFAULT_COVARIANCE}"),
                                sha256: artifact.sha256.clone(),
                            },
                            false,
                        ))
                    }
                    None => None,
                };
                match covariance {
                    Some((reference, explicit)) => {
                        let pathways = if self.iuncer == 2 {
                            "; the pathway tables of level 2 come from options.outputs, not this block"
                        } else {
                            ""
                        };
                        if explicit {
                            self.set_entry(
                                uncert_entry,
                                Status::Mapped,
                                format!("uncertainty.covariance = --covariance sidecar (MF=33 cross-section channel){pathways}"),
                            );
                        } else {
                            self.set_entry(
                                uncert_entry,
                                Status::Approximated,
                                format!("uncertainty from the default TENDL-2025 MF=33 covariance sidecar, not the deck's TENDL-2017 uncertainty data{pathways}"),
                            );
                        }
                        uncertainty = Some(json!({
                            "covariance": {"path": reference.path, "sha256": reference.sha256},
                        }));
                    }
                    None => self.set_entry(
                        uncert_entry,
                        Status::Unsupported,
                        "uncertainty needs a covariance sidecar paired with the library: pass --covariance FILE".into(),
                    ),
                }
            }
        } else if self.iuncer == 0 && uncert_entry.is_some() {
            self.set_entry(
                uncert_entry,
                Status::Noted,
                "level 0: no uncertainty output".into(),
            );
        }

        // material
        let (material, material_g) = match material {
            MaterialDef::Mass { total_g, elements } => (
                json!({"mass_g": total_g, "basis": "wt_percent", "composition": elements}),
                total_g,
            ),
            MaterialDef::Fuel {
                total_g,
                atoms_per_g,
            } => (
                json!({"mass_g": total_g, "basis": "atoms_per_g", "composition": atoms_per_g}),
                total_g,
            ),
        };

        let mut spec = Map::new();
        spec.insert("spec".into(), json!("actinv-spec-1"));
        if !self.title.is_empty() {
            spec.insert("title".into(), json!(self.title));
        }
        spec.insert("projectile".into(), json!(projectile.name()));
        spec.insert("library".into(), json!({ "path": library }));
        spec.insert("decay".into(), decay);
        let mut material = material;
        if has_power {
            // power_W_cm3 needs the density; FISPACT-II reads it from DENSITY
            if let Some(density) = self.density {
                material["density_g_cm3"] = json!(density);
            }
        }
        spec.insert("material".into(), material);
        spec.insert("spectrum".into(), Value::Object(spectrum));
        spec.insert("schedule".into(), Value::Array(schedule));
        if !projectile.is_neutron() {
            spec.insert("options".into(), json!({"temperature_K": 0.0}));
        }
        if let Some(uncertainty) = uncertainty {
            spec.insert("uncertainty".into(), uncertainty);
        }
        let mut photon = Map::new();
        if let Some(name) = self.photon.group_structure {
            photon.insert("group_structure".into(), json!(name));
        }
        if let Some(distance) = self.photon.point_source_m {
            photon.insert("point_source".into(), json!({"distance_m": distance}));
        }
        if !self.photon.bremsstrahlung.is_empty() {
            photon.insert(
                "bremsstrahlung".into(),
                json!({"nuclides": self.photon.bremsstrahlung}),
            );
        }
        if !photon.is_empty() {
            spec.insert("photon".into(), Value::Object(photon));
        }
        if !self.overrides.is_empty() {
            let items: Vec<Value> = self
                .overrides
                .iter()
                .map(|item| {
                    let mut map = Map::new();
                    map.insert("nuclide".into(), json!(item.nuclide));
                    map.insert("kind".into(), json!(item.kind));
                    if let Some(mt) = item.mt {
                        map.insert("mt".into(), json!(mt));
                    }
                    map.insert("value".into(), json!(item.value));
                    if let Some(units) = item.units {
                        map.insert("units".into(), json!(units));
                    }
                    Value::Object(map)
                })
                .collect();
            spec.insert("overrides".into(), Value::Array(items));
        }
        if self.a2_line.is_some() {
            let name = "us-49cfr173-a2";
            let sha256 = actinv_core::radiological::bundled_sha256(name)?;
            spec.insert(
                "radiological".into(),
                json!({"table": {"path": format!("bundled:{name}"), "sha256": sha256}}),
            );
        }
        if !self.pathway_entries.is_empty() {
            let blocker = if has_power {
                Some("power-driven schedule steps")
            } else if has_partition {
                Some("schedule partition")
            } else {
                None
            };
            match blocker {
                Some(blocker) => {
                    for index in std::mem::take(&mut self.pathway_entries) {
                        self.set_entry(
                            Some(index),
                            Status::Unsupported,
                            format!("ACTINV refuses pathways beside {blocker}, so no pathways section is emitted"),
                        );
                    }
                }
                None => {
                    let mut pathways = Map::new();
                    if !self.pathway_targets.is_empty() {
                        pathways.insert("targets".into(), json!(self.pathway_targets));
                    }
                    spec.insert("pathways".into(), Value::Object(pathways));
                }
            }
        }

        // A power-driven step needs a kerma table that no deck provides. The spec is emitted
        // without one and listed under required_additions; it is validated with a placeholder
        // table so that every other part of the document is still checked.
        let mut required: Vec<Value> = Vec::new();
        let mut validated = spec.clone();
        if has_power {
            validated.insert(
                "kerma".into(),
                json!({"table": {"path": "kerma-table-to-be-supplied", "sha256": "0".repeat(64)}}),
            );
            if self.density.is_none() {
                validated["material"]["density_g_cm3"] = json!(1.0);
                required.push(json!({
                    "field": "material.density_g_cm3",
                    "keyword": "POWER",
                    "line": self.power_line,
                    "reason": "POWER gives a power density (W/cc); the deck has no DENSITY, so add material.density_g_cm3",
                }));
            }
            required.push(json!({
                "field": "kerma",
                "keyword": "POWER",
                "line": self.power_line,
                "reason": "power-driven steps need kerma.table, a hash-pinned actinv-kerma-table-1 file (build one with scripts/build_kerma_table.py); until it is added the spec does not validate",
            }));
        }

        // Another lane owns the `summary` spec block; it is not part of the validated core spec
        // here, so check the document without it and attach it afterwards.
        let checked = serde_json::to_string(&Value::Object(validated))
            .map_err(|error| format!("cannot encode the converted problem: {error}"))?;
        Spec::from_json(&checked).map_err(|error| {
            format!("the converted problem is not a valid actinv-spec-1: {error}")
        })?;
        let mut summary = Map::new();
        if let Some(min_atoms) = self.min_atoms {
            summary.insert("min_atoms".into(), json!(min_atoms));
        }
        if let Some(include_stable) = self.include_stable {
            summary.insert("include_stable".into(), json!(include_stable));
        }
        if let Some(dominant_top) = self.dominant_top {
            summary.insert("dominant_top".into(), json!(dominant_top));
        }
        if !summary.is_empty() {
            spec.insert("summary".into(), Value::Object(summary));
        }

        // report
        let mut counts = Counts::default();
        let mut unsupported = Vec::new();
        let entries: Vec<Value> = self
            .entries
            .iter()
            .map(|entry| {
                match entry.status {
                    Status::Mapped => counts.mapped += 1,
                    Status::Approximated => counts.approximated += 1,
                    Status::Noted => counts.noted += 1,
                    Status::Unsupported => {
                        counts.unsupported += 1;
                        unsupported.push((entry.keyword.clone(), entry.line));
                    }
                }
                json!({
                    "keyword": entry.keyword,
                    "line": entry.line,
                    "phase": entry.phase.name(),
                    "status": entry.status.as_str(),
                    "detail": entry.detail,
                    "args": entry.args,
                })
            })
            .collect();
        let library_from = if defaulted_library {
            "default"
        } else {
            "--library"
        };
        let report = json!({
            "schema": "actinv-fispact-import-report-1",
            "title": self.title,
            "density_g_cm3": self.density,
            "counts": {
                "mapped": counts.mapped,
                "approximated": counts.approximated,
                "noted": counts.noted,
                "unsupported": counts.unsupported,
            },
            "spectrum": {
                "source": self.ctx.flux.as_ref().map(|flux| flux.path.clone()),
                "groups": groups,
                "structure": structure,
                "total_cm2_s": total,
                "wall_loading_mw_m2": self.ctx.flux.as_ref().map(|flux| flux.wall_loading_mw_m2),
                "title": self.ctx.flux.as_ref().map(|flux| flux.title.clone()),
                "rebin": rebin_report,
            },
            "material_mass_g": material_g,
            "library": {"path": library, "from": library_from},
            "steps": self.steps.len(),
            "schedule_map": schedule_map,
            "required_additions": required,
            "entries": entries,
        });
        Ok(Conversion {
            spec: Value::Object(spec),
            report,
            counts,
            unsupported,
        })
    }
}

/// Convert deck text to a spec plus its import report.
pub fn convert(deck: &str, ctx: &ImportContext) -> Result<Conversion, String> {
    let lexed = lex(deck);
    let mut parser = Parser {
        tokens: &lexed.tokens,
        pos: 0,
        phase: Phase::Library,
        ended: false,
    };
    let (nodes, _) = parser.parse_nodes(0)?;
    if !parser.ended {
        if let Some(error) = &lexed.error {
            return Err(error.clone());
        }
    }
    let mut interp = Interp::new(ctx);
    interp.run_nodes(&nodes, true)?;
    let ended = parser.ended;
    interp.finish(ended)
}

// ---------------------------------------------------------------------------------------------
// Files, flux files and the command line
// ---------------------------------------------------------------------------------------------

/// Parse a FISPACT-II `files` file: `key path` lines, `#` comments. Keys are lower-cased.
pub fn parse_files_file(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|line| {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                return None;
            }
            let mut words = line.split_whitespace();
            let key = words.next()?.to_ascii_lowercase();
            let value = words.next()?.to_string();
            Some((key, value))
        })
        .collect()
}

/// The group count of a FISPACT-II fluxes file: its numeric words, less the first-wall loading,
/// before the closing title line.
fn infer_group_count(text: &str) -> Result<usize, String> {
    let lines: Vec<&str> = text.lines().collect();
    let title = lines
        .iter()
        .rposition(|line| !line.trim().is_empty())
        .ok_or("the fluxes file is empty")?;
    let mut numeric = 0usize;
    for line in &lines[..title] {
        for word in line.split_whitespace() {
            if parse_number(word).is_none() {
                return Err(format!("non-numeric word '{word}' before the title line"));
            }
            numeric += 1;
        }
    }
    numeric
        .checked_sub(1)
        .filter(|groups| *groups > 0)
        .ok_or_else(|| "the fluxes file has no group values".to_string())
}

fn load_flux(path: &Path) -> Result<LoadedFlux, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read fluxes file {}: {error}", path.display()))?;
    let groups =
        infer_group_count(&text).map_err(|error| format!("{}: {error}", path.display()))?;
    let (values, wall_loading, title) = read_fispact_fluxes(path, groups)?;
    Ok(LoadedFlux {
        path: path.display().to_string(),
        values,
        wall_loading_mw_m2: wall_loading,
        title,
    })
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
    let deck_text = std::fs::read_to_string(deck_path)
        .map_err(|error| format!("cannot read {}: {error}", cli.deck))?;
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

    let flux_path: Option<PathBuf> = match &cli.fluxes {
        Some(path) => Some(PathBuf::from(path)),
        None => match files_entries.iter().rev().find(|(key, _)| key == "fluxes") {
            Some((_, value)) => Some(
                resolve_existing(value, &[files_dir.as_path(), deck_dir]).ok_or_else(|| {
                    format!("the fluxes file '{value}' named by the files file was not found; pass --fluxes FILE")
                })?,
            ),
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

    let conversion = convert(&deck_text, &ctx)?;
    let mut report = conversion.report;
    if let Value::Object(map) = &mut report {
        map.insert(
            "deck".into(),
            json!({"path": cli.deck, "sha256": sha256_file(deck_path)?}),
        );
        map.insert("strict".into(), json!(cli.strict));
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
    let report_file = report_path(&cli.out);
    let write = |path: &str, value: &Value| -> Result<(), String> {
        let text = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
        std::fs::write(path, text + "\n").map_err(|error| format!("cannot write {path}: {error}"))
    };
    write(&report_file, &report)?;
    let counts = conversion.counts;
    if cli.strict && counts.unsupported > 0 {
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
    use actinv_core::spec::parse_duration;

    fn flux709() -> LoadedFlux {
        LoadedFlux {
            path: "fluxes".into(),
            values: (0..709)
                .map(|group| if group >= 400 { 1.0e6 } else { 0.0 })
                .collect(),
            wall_loading_mw_m2: 1.0,
            title: "test spectrum".into(),
        }
    }

    fn context() -> ImportContext {
        ImportContext {
            flux: Some(flux709()),
            library: Some("library.npz".into()),
            decay: Some("decay.endf".into()),
            covariance: None,
        }
    }

    fn entries(conversion: &Conversion) -> &Vec<Value> {
        conversion.report["entries"].as_array().unwrap()
    }

    fn entry_status(conversion: &Conversion, keyword: &str) -> Vec<String> {
        entries(conversion)
            .iter()
            .filter(|entry| entry["keyword"] == keyword)
            .map(|entry| entry["status"].as_str().unwrap().to_string())
            .collect()
    }

    fn seconds(step: &Value) -> f64 {
        parse_duration(step["dt"].as_str().unwrap()).unwrap()
    }

    const FNS_FE: &str = "CLOBBER
GETXS 0
GETDECAY 0
FISPACT
* FNS 5 Min Fe
DENSITY 7.875
TOLER 0 1e1 2e-8
MASS 1.0E-3 1
FE 100.0
MIND 1.E3
HALF
HAZA
GRAPH 5 2 1 1 2 3 4 5
NUCGRAPH 1 0.01 1 2
FLUX 1.116E+10
ATOMS
TIME 5.0 MINS
ATOMS
FLUX 0.
NOSTABLE
ZERO
TIME 66  ATOMS
TIME 16  ATOMS
END
* END
/*
";

    #[test]
    fn lexer_strips_comments_and_keeps_star_lines() {
        let lexed = lex("FLUX 1 << inline >> ATOMS\n<< multi\nline * >> TIME 5 MINS\n* a title << not a comment\nEND\n");
        assert!(lexed.error.is_none());
        let words: Vec<(&str, bool)> = lexed
            .tokens
            .iter()
            .map(|token| (token.text.as_str(), token.star))
            .collect();
        assert_eq!(
            words,
            vec![
                ("FLUX", false),
                ("1", false),
                ("ATOMS", false),
                ("TIME", false),
                ("5", false),
                ("MINS", false),
                ("a title << not a comment", true),
                ("END", false),
            ]
        );
        assert_eq!(lexed.tokens[3].line, 3);
    }

    #[test]
    fn lexer_reports_an_unterminated_comment() {
        let lexed = lex("FLUX 1\n<< never closed\nTIME 5");
        assert_eq!(lexed.tokens.len(), 2);
        assert!(lexed.error.unwrap().contains("line 2"));
    }

    #[test]
    fn numbers_follow_fortran_conventions() {
        assert_eq!(parse_number("1.0E-3"), Some(1.0e-3));
        assert_eq!(parse_number("1.E3"), Some(1000.0));
        assert_eq!(parse_number("2D5"), Some(2.0e5));
        assert_eq!(parse_number("0."), Some(0.0));
        assert_eq!(parse_number("1.0+10"), Some(1.0e10));
        assert_eq!(parse_number("ATOMS"), None);
        assert_eq!(parse_number("inf"), None);
        assert_eq!(parse_number("Fe56"), None);
    }

    #[test]
    fn keywords_resolve_by_leading_characters() {
        assert_eq!(resolve_keyword("haza").unwrap().0, "HAZARDS");
        assert_eq!(resolve_keyword("UNCERT").unwrap().0, "UNCERTAINTY");
        assert_eq!(resolve_keyword("TOLER").unwrap().0, "TOLERANCE");
        assert_eq!(resolve_keyword("PATH").unwrap().0, "PATH");
        assert_eq!(resolve_keyword("PATHRESET").unwrap().0, "PATHRESET");
        assert_eq!(resolve_keyword("END").unwrap().0, "END");
        assert!(resolve_keyword("FROBNICATE").is_none());
        assert!(resolve_keyword("FE").is_none());
        assert!(resolve_keyword("Ti46").is_none());
        assert_eq!(time_unit("HOURS"), Some("h"));
        assert_eq!(time_unit("mins"), Some("min"));
        assert_eq!(time_unit("MIND"), None);
    }

    #[test]
    fn every_keyword_has_a_handler_or_a_disposition() {
        for (name, _) in KEYWORDS {
            assert!(
                HANDLED.contains(name) || simple_disposition(name).is_some(),
                "{name} has no disposition"
            );
        }
    }

    #[test]
    fn fns_deck_converts_to_a_valid_spec() {
        let conversion = convert(FNS_FE, &context()).unwrap();
        let spec = &conversion.spec;
        assert_eq!(spec["spec"], "actinv-spec-1");
        assert_eq!(spec["title"], "FNS 5 Min Fe");
        assert_eq!(spec["material"]["mass_g"], 1.0);
        assert_eq!(spec["material"]["basis"], "wt_percent");
        assert_eq!(spec["material"]["composition"]["Fe"], 100.0);
        assert_eq!(spec["spectrum"]["structure"], "fispact-709");
        assert_eq!(spec["spectrum"]["descending"], true);
        assert_eq!(spec["spectrum"]["total"], 1.116e10);
        let schedule = spec["schedule"].as_array().unwrap();
        assert_eq!(schedule.len(), 3);
        assert_eq!(seconds(&schedule[0]), 300.0);
        assert_eq!(schedule[0]["flux"], 1.0);
        assert_eq!(seconds(&schedule[1]), 66.0);
        assert_eq!(schedule[1]["flux"], 0.0);
        assert_eq!(spec["summary"]["min_atoms"], 1000.0);
        assert_eq!(spec["summary"]["include_stable"], false);
        assert!(spec["summary"].get("dominant_top").is_none());
        assert_eq!(conversion.counts.unsupported, 0);
        assert_eq!(entry_status(&conversion, "HAZARDS"), vec!["noted"]);
        assert_eq!(entry_status(&conversion, "MASS"), vec!["mapped"]);
        assert_eq!(entry_status(&conversion, "NOSTABLE"), vec!["approximated"]);
        assert_eq!(conversion.report["steps"], 3);
        assert_eq!(conversion.report["schedule_map"][0]["kind"], "irradiation");
        assert_eq!(conversion.report["schedule_map"][1]["kind"], "cooling");
    }

    #[test]
    fn comments_may_sit_anywhere_in_a_deck() {
        let deck = "<< -----initial----- >>
FISPACT
* title << kept as text
MASS 1E-3 << one element >> 1 FE 100 << iron >>
<< multi-line
   comment with FLUX 9e99 inside >>
FLUX 1E10 TIME 10 SECS ATOMS
END
";
        let conversion = convert(deck, &context()).unwrap();
        assert_eq!(conversion.spec["title"], "title << kept as text");
        assert_eq!(conversion.spec["spectrum"]["total"], 1.0e10);
        assert_eq!(conversion.spec["schedule"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn mass_percentages_become_a_weight_percent_composition() {
        let deck = "FISPACT
* inconel
MASS 2.0E-3 4
 NI 70.00
 MN  0.39
 fe  7.82
 CR 15.97
FLUX 1E10
TIME 1 HOURS ATOMS
END
";
        let conversion = convert(deck, &context()).unwrap();
        let material = &conversion.spec["material"];
        assert_eq!(material["mass_g"], 2.0);
        let composition = material["composition"].as_object().unwrap();
        assert_eq!(composition["Ni"], 70.0);
        assert_eq!(composition["Mn"], 0.39);
        assert_eq!(composition["Fe"], 7.82);
        assert_eq!(composition["Cr"], 15.97);
        // percentages that do not total 100 are said so, not rescaled
        let mass_entry = entries(&conversion)
            .iter()
            .find(|entry| entry["keyword"] == "MASS")
            .unwrap();
        assert!(mass_entry["detail"].as_str().unwrap().contains("not 100"));
    }

    #[test]
    fn mass_rejects_a_nuclide_and_an_unknown_element() {
        let nuclide = "FISPACT\n* t\nMASS 1E-3 1\n Fe56 100\nFLUX 1E10\nTIME 1 ATOMS\nEND\n";
        assert!(convert(nuclide, &context())
            .err()
            .unwrap()
            .contains("use FUEL"));
        let unknown = "FISPACT\n* t\nMASS 1E-3 1\n Qq 100\nFLUX 1E10\nTIME 1 ATOMS\nEND\n";
        assert!(convert(unknown, &context()).is_err());
        let short = "FISPACT\n* t\nMASS 1E-3 2\n Fe 100\n";
        assert!(convert(short, &context()).is_err());
    }

    #[test]
    fn time_units_convert_to_seconds() {
        let deck = "FISPACT
* units
MASS 1E-3 1
 FE 100
FLUX 1E10
TIME 90 ATOMS
TIME 2 MINS ATOMS
TIME 3 HOURS ATOMS
TIME 4 DAYS ATOMS
TIME 5 YEARS ATOMS
TIME 1.5 SECS ATOMS
END
";
        let conversion = convert(deck, &context()).unwrap();
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        let expected = [
            90.0,
            120.0,
            10_800.0,
            345_600.0,
            5.0 * 365.25 * 86_400.0,
            1.5,
        ];
        assert_eq!(schedule.len(), expected.len());
        for (step, want) in schedule.iter().zip(expected) {
            assert!((seconds(step) - want).abs() < 1e-9 * want, "{step}");
        }
    }

    #[test]
    fn flux_zero_and_zero_keyword_make_cooling_steps() {
        let deck = "FISPACT
* cooling
MASS 1E-3 1
 FE 100
FLUX 2E10
TIME 1 HOURS ATOMS
FLUX 1E10
TIME 1 HOURS ATOMS
FLUX 0
TIME 1 DAYS ATOMS
FLUX 2E10
TIME 1 HOURS ATOMS
ZERO
TIME 1 DAYS ATOMS
END
";
        let conversion = convert(deck, &context()).unwrap();
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        let multipliers: Vec<f64> = schedule
            .iter()
            .map(|step| step["flux"].as_f64().unwrap())
            .collect();
        // total = first positive FLUX (2E10); later fluxes are multipliers on it
        assert_eq!(multipliers, vec![1.0, 0.5, 0.0, 1.0, 0.0]);
        let kinds: Vec<&str> = conversion.report["schedule_map"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| step["kind"].as_str().unwrap())
            .collect();
        assert_eq!(
            kinds,
            vec![
                "irradiation",
                "irradiation",
                "cooling",
                "irradiation",
                "cooling"
            ]
        );
    }

    #[test]
    fn pulse_loops_expand_and_report_once() {
        let deck = "FISPACT
* pulses
MASS 1E-3 1
 FE 100
PULSE 3
 FLUX 0.0
 TIME 1.0 HOURS SPECTRUM
 FLUX 1.0E15
 TIME 1.0 HOURS SPECTRUM
ENDPULSE
FLUX 0.0
TIME 1.0 HOURS SPECTRUM
FLUX 1.0E15
TIME 1.0 HOURS ATOMS
END
";
        let conversion = convert(deck, &context()).unwrap();
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        let multipliers: Vec<f64> = schedule
            .iter()
            .map(|step| step["flux"].as_f64().unwrap())
            .collect();
        assert_eq!(multipliers, vec![0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]);
        // the loop body is reported once, not three times
        assert_eq!(entry_status(&conversion, "FLUX").len(), 4);
        assert_eq!(entry_status(&conversion, "PULSE"), vec!["mapped"]);
        let outputs: Vec<&str> = conversion.report["schedule_map"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| step["output"].as_str().unwrap())
            .collect();
        assert_eq!(outputs[0], "spectrum");
        assert_eq!(outputs[7], "atoms");
    }

    #[test]
    fn nested_pulses_multiply_and_unclosed_ones_fail() {
        let nested = "FISPACT
* nested
MASS 1E-3 1
 FE 100
FLUX 1E10
PULSE 2
 PULSE 3
  TIME 1 SECS STEP
 ENDPULSE
 TIME 1 HOURS STEP
ENDPULSE
END
";
        let conversion = convert(nested, &context()).unwrap();
        assert_eq!(conversion.spec["schedule"].as_array().unwrap().len(), 8);
        let open = "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nPULSE 2\n FLUX 1E10\n TIME 1 ATOMS\nEND\n";
        assert!(convert(open, &context())
            .err()
            .unwrap()
            .contains("ENDPULSE"));
        let stray = "FISPACT\n* t\nENDPULSE\nEND\n";
        assert!(convert(stray, &context())
            .err()
            .unwrap()
            .contains("without"));
    }

    #[test]
    fn unknown_and_unsupported_keywords_appear_in_the_report() {
        let deck = "FISPACT
* odd
MASS 1E-3 1
 FE 100
FROBNICATE 1 2 3
SSFGEOMETRY 1 0.5
IRON
FLUX 1E10
TIME 1 HOURS ATOMS
END
";
        let conversion = convert(deck, &context()).unwrap();
        assert_eq!(entry_status(&conversion, "FROBNICATE"), vec!["unsupported"]);
        assert_eq!(
            entry_status(&conversion, "SSFGEOMETRY"),
            vec!["unsupported"]
        );
        assert_eq!(entry_status(&conversion, "IRON"), vec!["unsupported"]);
        assert_eq!(conversion.counts.unsupported, 3);
        assert_eq!(
            conversion.unsupported,
            vec![
                ("FROBNICATE".to_string(), 5),
                ("SSFGEOMETRY".to_string(), 6),
                ("IRON".to_string(), 7)
            ]
        );
        // the numbers were arguments of SSFGEOMETRY, not keywords of their own
        assert!(entry_status(&conversion, "1").is_empty());
        let frob = entries(&conversion)
            .iter()
            .find(|entry| entry["keyword"] == "FROBNICATE")
            .unwrap();
        assert_eq!(frob["line"], 5);
        assert_eq!(frob["args"], "1 2 3");
    }

    #[test]
    fn fuel_becomes_atoms_per_gram() {
        let deck = "FISPACT
* lithium
DENSITY 0.5
FUEL 2
 Li6 8.5E24
 TE129N 0.0
FLUX 1E10
TIME 1 HOURS ATOMS
END
";
        // TE129N is the second isomer; mixing elements is fine as these are distinct elements
        let conversion = convert(deck, &context()).unwrap();
        let material = &conversion.spec["material"];
        assert_eq!(material["basis"], "atoms_per_g");
        let composition = material["composition"].as_object().unwrap();
        assert!(composition.contains_key("Te129m2"));
        let mass_g = material["mass_g"].as_f64().unwrap();
        assert!((mass_g - 8.5e24 * 6.0 / NA).abs() < 1e-9 * mass_g);
        let atoms = composition["Li6"].as_f64().unwrap() * mass_g;
        assert!((atoms - 8.5e24).abs() < 1e-9 * 8.5e24);
        assert_eq!(entry_status(&conversion, "FUEL"), vec!["approximated"]);
    }

    #[test]
    fn material_is_required_and_defined_once() {
        let none = "FISPACT\n* t\nFLUX 1E10\nTIME 1 ATOMS\nEND\n";
        assert!(convert(none, &context())
            .err()
            .unwrap()
            .contains("MASS or FUEL"));
        let twice =
            "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFUEL 1\n Li6 1E20\nFLUX 1E10\nTIME 1 ATOMS\nEND\n";
        assert!(convert(twice, &context()).err().unwrap().contains("once"));
    }

    #[test]
    fn an_irradiating_deck_without_a_flux_file_is_refused() {
        let deck = "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let ctx = ImportContext {
            flux: None,
            ..context()
        };
        let error = convert(deck, &ctx).err().unwrap();
        assert!(error.contains("--fluxes"), "{error}");
    }

    #[test]
    fn getxs_group_count_must_match_the_flux_file() {
        let deck =
            "GETXS 1 162\nFISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let error = convert(deck, &context()).err().unwrap();
        assert!(error.contains("162"), "{error}");
    }

    #[test]
    fn uncertainty_follows_the_covariance_source() {
        let deck = "FISPACT
* u
MASS 1E-3 1
 FE 100
UNCERT 2
FLUX 1E10
TIME 1 HOURS ATOMS
END
";
        // a custom library with no covariance cannot carry an uncertainty block
        let conversion = convert(deck, &context()).unwrap();
        assert!(conversion.spec.get("uncertainty").is_none());
        assert_eq!(
            entry_status(&conversion, "UNCERTAINTY"),
            vec!["unsupported"]
        );
        // an explicit sidecar maps it
        let mut ctx = context();
        ctx.covariance = Some(CovarianceRef {
            path: "cov.npz".into(),
            sha256: "ab".repeat(32),
        });
        let conversion = convert(deck, &ctx).unwrap();
        assert_eq!(
            conversion.spec["uncertainty"]["covariance"]["path"],
            "cov.npz"
        );
        assert_eq!(entry_status(&conversion, "UNCERTAINTY"), vec!["mapped"]);
        // NOERROR wins
        let silenced = deck.replace("FISPACT", "NOERROR\nFISPACT");
        let conversion = convert(&silenced, &ctx).unwrap();
        assert!(conversion.spec.get("uncertainty").is_none());
        assert_eq!(entry_status(&conversion, "UNCERTAINTY"), vec!["noted"]);
    }

    #[test]
    fn default_library_uses_catalog_references_and_the_catalog_covariance() {
        let deck =
            "FISPACT\n* d\nMASS 1E-3 1\n FE 100\nUNCERT 1\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let ctx = ImportContext {
            library: None,
            decay: None,
            ..context()
        };
        let conversion = convert(deck, &ctx).unwrap();
        assert_eq!(
            conversion.spec["library"]["path"],
            "catalog:tendl-2025-neutron-709g"
        );
        assert_eq!(
            conversion.spec["decay"]["primary"],
            "catalog:endfb-viii-0-decay"
        );
        assert_eq!(
            conversion.spec["decay"]["fallback"],
            "catalog:jeff-3-3-decay"
        );
        assert_eq!(
            conversion.spec["uncertainty"]["covariance"]["path"],
            "catalog:tendl-2025-neutron-709g-covariance"
        );
        assert_eq!(
            entry_status(&conversion, "UNCERTAINTY"),
            vec!["approximated"]
        );
    }

    #[test]
    fn charged_particle_projectiles_select_the_162_group_structure() {
        let deck = "PROJECTILE 3\nFISPACT\n* p\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let ctx = ImportContext {
            flux: Some(LoadedFlux {
                values: vec![1.0; 162],
                ..flux709()
            }),
            ..context()
        };
        let conversion = convert(deck, &ctx).unwrap();
        assert_eq!(conversion.spec["projectile"], "proton");
        assert_eq!(conversion.spec["spectrum"]["structure"], "fispact-162");
        assert_eq!(conversion.spec["options"]["temperature_K"], 0.0);
        // PROJECTILE 6 and 7 map to triton and helion (P128) on the same 162-group grid.
        for (code, name) in [("6", "triton"), ("7", "helion")] {
            let charged = deck.replace("PROJECTILE 3", &format!("PROJECTILE {code}"));
            let conversion = convert(&charged, &ctx).ok().unwrap();
            assert_eq!(conversion.spec["projectile"], name);
            assert_eq!(conversion.spec["spectrum"]["structure"], "fispact-162");
        }
        let unknown = deck.replace("PROJECTILE 3", "PROJECTILE 8");
        let error = convert(&unknown, &ctx).err().unwrap();
        assert!(error.contains("PROJECTILE 8"), "{error}");
    }

    #[test]
    fn summary_block_carries_mind_nostable_and_sortdominant() {
        let deck = "FISPACT
* s
MIND 1E5
NOSTABLE
SORTDOMINANT 30 10
MASS 1E-3 1
 FE 100
FLUX 1E10
TIME 1 HOURS ATOMS
END
";
        let conversion = convert(deck, &context()).unwrap();
        assert_eq!(conversion.spec["summary"]["min_atoms"], 1.0e5);
        assert_eq!(conversion.spec["summary"]["include_stable"], false);
        assert_eq!(conversion.spec["summary"]["dominant_top"], 10);
        assert_eq!(entry_status(&conversion, "NOSTABLE"), vec!["mapped"]);
        assert_eq!(
            entry_status(&conversion, "SORTDOMINANT"),
            vec!["approximated"]
        );
        let plain = "FISPACT\n* s\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        assert!(convert(plain, &context())
            .unwrap()
            .spec
            .get("summary")
            .is_none());
    }

    #[test]
    fn a_time_without_an_output_keyword_never_runs() {
        let deck = "FISPACT
* t
MASS 1E-3 1
 FE 100
FLUX 1E10
TIME 1 HOURS
TIME 2 HOURS ATOMS
TIME 3 HOURS
END
";
        let conversion = convert(deck, &context()).unwrap();
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        assert_eq!(schedule.len(), 1);
        assert_eq!(seconds(&schedule[0]), 7200.0);
        let time = entries(&conversion)
            .iter()
            .find(|entry| entry["keyword"] == "TIME" && entry["line"] == 7)
            .unwrap();
        assert!(time["detail"].as_str().unwrap().contains("replaces"));
    }

    #[test]
    fn files_file_and_flux_file_parsing() {
        let files = "# comment\nxs_endf  /data/gxs-709\n\nFLUXES my_fluxes\narb_flux spectra\n";
        assert_eq!(
            parse_files_file(files),
            vec![
                ("xs_endf".to_string(), "/data/gxs-709".to_string()),
                ("fluxes".to_string(), "my_fluxes".to_string()),
                ("arb_flux".to_string(), "spectra".to_string()),
            ]
        );
        // 7 values, a wall loading and a title: the last group-value line holds a single word
        let fluxes = " 1.0 2.0 3.0\n 4.0 5.0 6.0\n 7.0\n 1.000\nPosition #2 Normalization Factor 5.634e+11\n";
        assert_eq!(infer_group_count(fluxes).unwrap(), 7);
        assert!(infer_group_count("1.0 x\ntitle\n").is_err());
        assert!(infer_group_count("title only\n").is_err());
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

    /// A one-element deck around `body`: the keywords between the material and `END`. `body`
    /// starts on line 5.
    fn deck_with(body: &str) -> String {
        format!("FISPACT\n* t\nMASS 1E-3 1\n FE 100\n{body}\nEND\n")
    }

    fn entry_detail(conversion: &Conversion, keyword: &str) -> String {
        entries(conversion)
            .iter()
            .find(|entry| entry["keyword"] == keyword)
            .map(|entry| entry["detail"].as_str().unwrap().to_string())
            .unwrap_or_default()
    }

    fn context_with_covariance() -> ImportContext {
        ImportContext {
            covariance: Some(CovarianceRef {
                path: "cov.npz".into(),
                sha256: "ab".repeat(32),
            }),
            ..context()
        }
    }

    #[test]
    fn power_steps_emit_the_target_and_list_the_missing_kerma_table() {
        // POWER p n mt..: p is a power density in W/cc; DENSITY supplies the g/cm3 it needs
        let deck = deck_with(
            "DENSITY 7.874\nPOWER 150.0 1 301\nTIME 1 HOURS ATOMS\nFLUX 0\nTIME 1 DAYS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        assert_eq!(schedule[0]["power_W_cm3"], 150.0);
        assert!(schedule[0].get("flux").is_none() && schedule[0].get("power_W_per_g").is_none());
        assert_eq!(schedule[1]["flux"], 0.0);
        assert_eq!(conversion.spec["material"]["density_g_cm3"], 7.874);
        assert!(conversion.spec.get("kerma").is_none());
        assert!(conversion.spec["spectrum"].get("total").is_none());
        assert_eq!(entry_status(&conversion, "POWER"), vec!["approximated"]);
        assert!(!entry_detail(&conversion, "POWER").contains("MT list"));
        let required = conversion.report["required_additions"].as_array().unwrap();
        assert_eq!(required.len(), 1);
        assert_eq!(required[0]["field"], "kerma");
        assert_eq!(required[0]["line"], 6);
        let map = &conversion.report["schedule_map"];
        assert_eq!(map[0]["kind"], "irradiation");
        assert_eq!(map[0]["power_W_cm3"], 150.0);
        assert_eq!(map[1]["kind"], "cooling");
        // adding the table makes the converted problem valid
        let mut spec = conversion.spec.clone();
        spec["kerma"] = json!({"table": {"path": "k.json", "sha256": "ab".repeat(32)}});
        Spec::from_json(&spec.to_string()).unwrap();
        // without DENSITY the density is a required addition too; an MT list other than 301
        // is named, since ACTINV heats from the table's total kerma
        let bare = convert(
            &deck_with("POWER 150.0 2 301 444\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        let required = bare.report["required_additions"].as_array().unwrap();
        let fields: Vec<&str> = required
            .iter()
            .map(|r| r["field"].as_str().unwrap())
            .collect();
        assert_eq!(fields, vec!["material.density_g_cm3", "kerma"]);
        assert!(bare.spec["material"].get("density_g_cm3").is_none());
        assert!(entry_detail(&bare, "POWER").contains("MT list '2 301 444'"));
        // a deck with no POWER lists nothing
        let plain = convert(&deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS"), &context()).unwrap();
        assert!(plain.report["required_additions"]
            .as_array()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn power_steps_drop_the_features_the_spec_refuses_beside_them() {
        let deck = deck_with("UNCERT 2\nPATH 1 MN54\nPOWER 150.0\nTIME 1 HOURS ATOMS");
        let conversion = convert(&deck, &context_with_covariance()).unwrap();
        assert!(conversion.spec.get("uncertainty").is_none());
        assert!(conversion.spec.get("pathways").is_none());
        assert_eq!(
            entry_status(&conversion, "UNCERTAINTY"),
            vec!["unsupported"]
        );
        assert_eq!(entry_status(&conversion, "PATH"), vec!["unsupported"]);
        assert!(entry_detail(&conversion, "UNCERTAINTY").contains("power-driven"));
        assert!(entry_detail(&conversion, "PATH").contains("power-driven"));
        // a missing or negative power is reported, not guessed
        let missing = convert(
            &deck_with("POWER\nFLUX 1E10\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        assert_eq!(entry_status(&missing, "POWER"), vec!["unsupported"]);
        assert!(missing.report["required_additions"]
            .as_array()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn wall_steps_scale_the_spectrum_files_own_flux() {
        let deck = deck_with(
            "FLUX 1E10\nTIME 1 HOURS ATOMS\nWALL 2.0\nTIME 1 HOURS ATOMS\nZERO\nTIME 1 DAYS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        let sum: f64 = flux709().values.iter().sum();
        let spectrum = &conversion.spec["spectrum"];
        assert_eq!(spectrum["wall_loading_MW_m2"], 1.0);
        // FLUX multipliers are taken against the file's own flux, as WALL's are
        assert_eq!(spectrum["total"], sum);
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        assert_eq!(schedule[0]["flux"], 1.0e10 / sum);
        assert_eq!(schedule[1]["wall_loading_MW_m2"], 2.0);
        assert!(schedule[1].get("flux").is_none());
        assert_eq!(schedule[2]["flux"], 0.0);
        assert_eq!(entry_status(&conversion, "WALL"), vec!["mapped"]);
        assert_eq!(
            conversion.report["schedule_map"][1]["wall_loading_MW_m2"],
            2.0
        );
        // a file with no first-wall loading falls back to the 14 MeV current equivalence
        let ctx = ImportContext {
            flux: Some(LoadedFlux {
                wall_loading_mw_m2: 0.0,
                ..flux709()
            }),
            ..context()
        };
        let fallback = convert(&deck_with("WALL 2.0\nTIME 1 HOURS ATOMS"), &ctx).unwrap();
        assert!(fallback.spec["spectrum"]
            .get("wall_loading_MW_m2")
            .is_none());
        assert_eq!(entry_status(&fallback, "WALL"), vec!["approximated"]);
        assert!(entry_detail(&fallback, "WALL").contains("4.44e13"));
        // WALL 0 is cooling
        let cooling = convert(
            &deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS\nWALL 0\nTIME 1 DAYS ATOMS"),
            &context(),
        )
        .unwrap();
        assert_eq!(cooling.spec["schedule"][1]["flux"], 0.0);
    }

    #[test]
    fn partition_attaches_to_the_next_step_and_repeats_multiply() {
        let deck = deck_with(
            "FLUX 1E10\nTIME 1 HOURS ATOMS\nFLUX 0\nPARTITION 2\n MN 0.25\n CR 0.5\nPARTITION MN 0.5\nTIME 1 DAYS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        assert!(schedule[0].get("partition").is_none());
        assert_eq!(schedule[1]["partition"], json!({"Cr": 0.5, "Mn": 0.125}));
        assert_eq!(
            entry_status(&conversion, "PARTITION"),
            vec!["mapped", "mapped"]
        );
        // a partition no step follows, an unknown element and a fraction above one are reported
        let trailing = convert(
            &deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS\nPARTITION AR 0.01"),
            &context(),
        )
        .unwrap();
        assert_eq!(entry_status(&trailing, "PARTITION"), vec!["unsupported"]);
        assert!(trailing.spec["schedule"][0].get("partition").is_none());
        for bad in [
            "PARTITION QQ 0.5",
            "PARTITION FE 1.5",
            "PARTITION FE",
            "PARTITION 2 FE 0.5",
        ] {
            let deck = deck_with(&format!("FLUX 1E10\n{bad}\nTIME 1 HOURS ATOMS"));
            let conversion = convert(&deck, &context()).unwrap();
            assert_eq!(
                entry_status(&conversion, "PARTITION"),
                vec!["unsupported"],
                "{bad}"
            );
            assert!(conversion.spec["schedule"][0].get("partition").is_none());
        }
    }

    #[test]
    fn over_blocks_become_named_overrides() {
        let deck = deck_with(
            "OVER FE56\n ACROSS MN56 1.0E-2\n ACROSS FE55 0.5\nOVER MN56\n ALAM 100.0 MINS\n ADLAM 0.1\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        let overrides = conversion.spec["overrides"].as_array().unwrap();
        assert_eq!(overrides.len(), 3);
        // Fe56 -> Mn56 is (n,p), Fe56 -> Fe55 is (n,2n)
        assert_eq!(
            overrides[0],
            json!({"nuclide": "Fe56", "kind": "cross_section", "mt": 103, "value": 0.01})
        );
        assert_eq!(
            overrides[1],
            json!({"nuclide": "Fe56", "kind": "cross_section", "mt": 16, "value": 0.5})
        );
        assert_eq!(
            overrides[2],
            json!({"nuclide": "Mn56", "kind": "half_life", "value": 100.0, "units": "min"})
        );
        assert_eq!(
            entry_status(&conversion, "OVER"),
            vec!["approximated", "approximated"]
        );
        assert_eq!(
            entry_status(&conversion, "ACROSS"),
            vec!["approximated", "approximated"]
        );
        assert_eq!(entry_status(&conversion, "ALAM"), vec!["mapped"]);
        // the error-factor sub-keywords are reported, not converted
        assert_eq!(entry_status(&conversion, "ADLAM"), vec!["unsupported"]);
        assert_eq!(conversion.counts.unsupported, 1);
    }

    #[test]
    fn over_reads_the_conderc_fission_deck_form() {
        let deck = "FISPACT\n* t\nFUEL 1\n U235 1.0E+24\nOVER U235\n  ACROSS U236 0.0\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let conversion = convert(deck, &context()).unwrap();
        assert_eq!(
            conversion.spec["overrides"][0],
            json!({"nuclide": "U235", "kind": "cross_section", "mt": 102, "value": 0.0})
        );
    }

    #[test]
    fn over_reports_what_it_cannot_select() {
        // Mn54 is (n,t) or (n,nd), Mn56m is an isomer, a negative cross section is not one
        let deck = deck_with(
            "OVER FE56\n ACROSS MN54 1.0\n ACROSS MN56M 1.0\n ACROSS FE57 -1.0\n ADCROSS FE57 0.1\n ALAM\nOVER\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        assert!(conversion.spec.get("overrides").is_none());
        assert_eq!(
            entry_status(&conversion, "ACROSS"),
            vec!["unsupported", "unsupported", "unsupported"]
        );
        assert_eq!(entry_status(&conversion, "ADCROSS"), vec!["unsupported"]);
        assert_eq!(entry_status(&conversion, "ALAM"), vec!["unsupported"]);
        assert_eq!(
            entry_status(&conversion, "OVER"),
            vec!["unsupported", "unsupported"]
        );
        // a second, different value for one reaction is refused rather than overwritten
        let twice = deck_with(
            "OVER FE56\n ACROSS MN56 1.0\n ACROSS MN56 2.0\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let conversion = convert(&twice, &context()).unwrap();
        assert_eq!(conversion.spec["overrides"].as_array().unwrap().len(), 1);
        assert_eq!(
            entry_status(&conversion, "ACROSS"),
            vec!["approximated", "unsupported"]
        );
        // charged-particle reactions are not selected by this neutron mapping
        let ctx = ImportContext {
            flux: Some(LoadedFlux {
                values: vec![1.0; 162],
                ..flux709()
            }),
            ..context()
        };
        let proton = format!(
            "PROJECTILE 3\n{}",
            deck_with("OVER FE56\n ACROSS CO57 1.0\nFLUX 1E10\nTIME 1 HOURS ATOMS")
        );
        let conversion = convert(&proton, &ctx).unwrap();
        assert!(conversion.spec.get("overrides").is_none());
        assert_eq!(entry_status(&conversion, "ACROSS"), vec!["unsupported"]);
    }

    #[test]
    fn cross_section_overrides_drop_the_uncertainty_block() {
        let deck =
            deck_with("OVER FE56\n ACROSS MN56 1.0E-2\nUNCERT 2\nFLUX 1E10\nTIME 1 HOURS ATOMS");
        let conversion = convert(&deck, &context_with_covariance()).unwrap();
        assert_eq!(conversion.spec["overrides"].as_array().unwrap().len(), 1);
        assert!(conversion.spec.get("uncertainty").is_none());
        assert_eq!(
            entry_status(&conversion, "UNCERTAINTY"),
            vec!["unsupported"]
        );
        // a half-life override leaves uncertainty alone
        let half_life =
            deck_with("OVER MN56\n ALAM 2.5 HOURS\nUNCERT 2\nFLUX 1E10\nTIME 1 HOURS ATOMS");
        let conversion = convert(&half_life, &context_with_covariance()).unwrap();
        assert!(conversion.spec.get("uncertainty").is_some());
        assert_eq!(
            conversion.spec["overrides"][0],
            json!({"nuclide": "Mn56", "kind": "half_life", "value": 2.5, "units": "h"})
        );
    }

    fn ctx_with_groups(groups: usize) -> ImportContext {
        ImportContext {
            flux: Some(LoadedFlux {
                values: vec![1.0; groups],
                ..flux709()
            }),
            ..context()
        }
    }

    #[test]
    fn grpconvert_converts_a_standard_structure_spectrum() {
        let deck = "GRPCONVERT 175 709\nCNVTYPE 1\nLIMGRP 1.0E-3 2.0E7\nFISPACT\n* g\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let conversion = convert(deck, &ctx_with_groups(175)).unwrap();
        let spectrum = &conversion.spec["spectrum"];
        assert_eq!(spectrum["structure"], "fispact-709");
        assert_eq!(spectrum["boundaries_eV"].as_array().unwrap().len(), 176);
        assert_eq!(spectrum["flux_per_group"].as_array().unwrap().len(), 175);
        assert_eq!(spectrum["descending"], true);
        assert_eq!(spectrum["rebin"]["convention"], "equal_energy");
        assert_eq!(spectrum["rebin"]["emin_eV"], 1.0e-3);
        assert_eq!(spectrum["rebin"]["emax_eV"], 2.0e7);
        assert_eq!(conversion.report["spectrum"]["rebin"]["source_groups"], 175);
        assert_eq!(entry_status(&conversion, "GRPCONVERT"), vec!["mapped"]);
        assert_eq!(entry_status(&conversion, "CNVTYPE"), vec!["mapped"]);
        assert_eq!(entry_status(&conversion, "LIMGRP"), vec!["approximated"]);
        // the default convention is equal lethargy, and no LIMGRP leaves the limits out
        let plain = "GRPCONVERT 175 709\nFISPACT\n* g\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let conversion = convert(plain, &ctx_with_groups(175)).unwrap();
        let rebin = &conversion.spec["spectrum"]["rebin"];
        assert_eq!(rebin["convention"], "equal_lethargy");
        assert!(rebin.get("emin_eV").is_none());
        assert!(rebin.get("emax_eV").is_none());
    }

    #[test]
    fn grpconvert_needs_a_known_source_structure() {
        let body = "FISPACT\n* g\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        // a 175-group file without GRPCONVERT still stops the import, and names the keyword
        let error = convert(body, &ctx_with_groups(175)).err().unwrap();
        assert!(error.contains("GRPCONVERT"), "{error}");
        // 173 groups match no FISPACT-II standard structure
        let error = convert(
            &format!("GRPCONVERT 173 709\n{body}"),
            &ctx_with_groups(173),
        )
        .err()
        .unwrap();
        assert!(error.contains("no FISPACT-II standard"), "{error}");
        // a file already in the library structure needs no conversion
        let conversion = convert(&format!("GRPCONVERT 709 709\n{body}"), &context()).unwrap();
        assert!(conversion.spec["spectrum"].get("rebin").is_none());
        assert_eq!(entry_status(&conversion, "GRPCONVERT"), vec!["noted"]);
        // CNVTYPE alone converts nothing; LIMGRP alone limits the library-structure spectrum
        let conversion = convert(&format!("CNVTYPE 1\n{body}"), &context()).unwrap();
        assert!(conversion.spec["spectrum"].get("rebin").is_none());
        assert_eq!(entry_status(&conversion, "CNVTYPE"), vec!["noted"]);
        let conversion = convert(&format!("LIMGRP 1.0 1.0E6\n{body}"), &context()).unwrap();
        assert_eq!(conversion.spec["spectrum"]["rebin"]["emax_eV"], 1.0e6);
        assert_eq!(
            conversion.spec["spectrum"]["boundaries_eV"]
                .as_array()
                .unwrap()
                .len(),
            710
        );
        // malformed arguments are reported
        let conversion =
            convert(&format!("CNVTYPE 2\nLIMGRP 5.0 1.0\n{body}"), &context()).unwrap();
        assert_eq!(entry_status(&conversion, "CNVTYPE"), vec!["unsupported"]);
        assert_eq!(entry_status(&conversion, "LIMGRP"), vec!["unsupported"]);
        assert!(conversion.spec["spectrum"].get("rebin").is_none());
    }

    #[test]
    fn dose_group_and_bremsstrahlung_fill_the_photon_section() {
        let deck = deck_with(
            "DOSE 2 0.5\nGROUP 1\nBREMSSTRAHLUNG 2 CL36 K42\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        let photon = &conversion.spec["photon"];
        assert_eq!(photon["point_source"]["distance_m"], 0.5);
        assert_eq!(photon["group_structure"], "steiner-22");
        assert_eq!(photon["bremsstrahlung"]["nuclides"], json!(["Cl36", "K42"]));
        assert_eq!(entry_status(&conversion, "DOSE"), vec!["mapped"]);
        assert_eq!(entry_status(&conversion, "GROUP"), vec!["mapped"]);
        assert_eq!(
            entry_status(&conversion, "BREMSSTRAHLUNG"),
            vec!["approximated"]
        );
        assert!(entry_detail(&conversion, "BREMSSTRAHLUNG").contains("separate"));
        // FISPACT-II raises a distance below 0.3 m to 0.3 m
        let close = convert(
            &deck_with("DOSE 2 0.1\nFLUX 1E10\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        assert_eq!(close.spec["photon"]["point_source"]["distance_m"], 0.3);
        assert_eq!(entry_status(&close, "DOSE"), vec!["approximated"]);
        // DOSE 1 is the default contact dose and, later, undoes DOSE 2
        let contact = convert(
            &deck_with("DOSE 2 0.5\nDOSE 1\nFLUX 1E10\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        assert!(contact.spec.get("photon").is_none());
        assert_eq!(entry_status(&contact, "DOSE"), vec!["mapped", "noted"]);
        // GROUP 0 is the 24-group default
        let default_groups = convert(
            &deck_with("GROUP 0\nFLUX 1E10\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        assert_eq!(
            default_groups.spec["photon"]["group_structure"],
            "fispact-24"
        );
    }

    #[test]
    fn photon_keywords_report_what_they_cannot_map() {
        let run = "FLUX 1E10\nTIME 1 HOURS ATOMS";
        let no_distance = convert(&deck_with(&format!("DOSE 2\n{run}")), &context()).unwrap();
        assert_eq!(entry_status(&no_distance, "DOSE"), vec!["unsupported"]);
        assert!(no_distance.spec.get("photon").is_none());
        let other_geometry =
            convert(&deck_with(&format!("DOSE 3 1.0\n{run}")), &context()).unwrap();
        assert_eq!(entry_status(&other_geometry, "DOSE"), vec!["unsupported"]);
        let group = convert(&deck_with(&format!("GROUP 7\n{run}")), &context()).unwrap();
        assert_eq!(entry_status(&group, "GROUP"), vec!["unsupported"]);
        assert!(group.spec.get("photon").is_none());
        for bad in [
            "BREMSSTRAHLUNG FE",
            "BREMSSTRAHLUNG 3 CL36",
            "BREMSSTRAHLUNG",
            "BREMSSTRAHLUNG QQ99",
        ] {
            let conversion = convert(&deck_with(&format!("{bad}\n{run}")), &context()).unwrap();
            assert_eq!(
                entry_status(&conversion, "BREMSSTRAHLUNG"),
                vec!["unsupported"],
                "{bad}"
            );
            assert!(conversion.spec.get("photon").is_none(), "{bad}");
        }
    }

    #[test]
    fn path_and_routes_name_the_pathway_targets() {
        let deck =
            deck_with("PATH 2 MN54 CO60\nROUTES MN54 FE55 0.5\nFLUX 1E10\nTIME 1 HOURS ATOMS");
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(
            conversion.spec["pathways"],
            json!({"targets": ["Mn54", "Co60", "Fe55"]})
        );
        assert_eq!(entry_status(&conversion, "PATH"), vec!["approximated"]);
        assert_eq!(entry_status(&conversion, "ROUTES"), vec!["approximated"]);
        // the leading count is the count; the 0.5 is a setting the importer does not read
        assert!(!entry_detail(&conversion, "PATH").contains("not interpreted"));
        assert!(entry_detail(&conversion, "ROUTES").contains("0.5"));
        // no nuclide named: the dominant targets are chosen automatically
        let bare = convert(
            &deck_with("PATH\nFLUX 1E10\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        assert_eq!(bare.spec["pathways"], json!({}));
        // partition cannot be combined with pathways
        let split = convert(
            &deck_with(
                "PATH 1 MN54\nFLUX 1E10\nTIME 1 HOURS ATOMS\nPARTITION MN 0.5\nTIME 1 HOURS ATOMS",
            ),
            &context(),
        )
        .unwrap();
        assert!(split.spec.get("pathways").is_none());
        assert!(split.spec["schedule"][1].get("partition").is_some());
        assert_eq!(entry_status(&split, "PATH"), vec!["unsupported"]);
        assert!(entry_detail(&split, "PATH").contains("partition"));
    }

    #[test]
    fn atwo_maps_to_the_bundled_a2_table() {
        let deck = deck_with("ATWO\nCLEAR\nHAZA\nFLUX 1E10\nTIME 1 HOURS ATOMS");
        let conversion = convert(&deck, &context()).unwrap();
        let table = &conversion.spec["radiological"]["table"];
        assert_eq!(table["path"], "bundled:us-49cfr173-a2");
        assert_eq!(
            table["sha256"],
            actinv_core::radiological::bundled_sha256("us-49cfr173-a2")
                .unwrap()
                .as_str()
        );
        assert_eq!(entry_status(&conversion, "ATWO"), vec!["approximated"]);
        // no jurisdiction or dose table is chosen for CLEAR and HAZARDS
        assert_eq!(entry_status(&conversion, "CLEAR"), vec!["noted"]);
        assert_eq!(entry_status(&conversion, "HAZARDS"), vec!["noted"]);
        assert!(entry_detail(&conversion, "CLEAR").contains("jurisdiction"));
        let none = convert(&deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS"), &context()).unwrap();
        assert!(none.spec.get("radiological").is_none());
    }

    #[test]
    fn a_mid_run_getxs_names_the_step_to_edit() {
        let deck = deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS\nGETXS 1 709\nTIME 1 HOURS ATOMS");
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(entry_status(&conversion, "GETXS"), vec!["unsupported"]);
        assert!(entry_detail(&conversion, "GETXS").contains("schedule[1]"));
        assert_eq!(conversion.spec["schedule"].as_array().unwrap().len(), 2);
        assert!(conversion.spec["schedule"][1].get("library").is_none());
    }

    #[test]
    fn self_shielding_keywords_say_which_data_the_deck_lacks() {
        let deck = deck_with(
            "SSFGEOMETRY 1 0.01\nSSFCHOOSE 1 FE\nSSFDILUTION FE56 1 10.0\nPROBTABLE 1\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        assert!(conversion.spec.get("self_shielding").is_none());
        for keyword in ["SSFGEOMETRY", "SSFCHOOSE", "SSFDILUTION", "PROBTABLE"] {
            assert_eq!(
                entry_status(&conversion, keyword),
                vec!["unsupported"],
                "{keyword}"
            );
            assert!(
                entry_detail(&conversion, keyword).contains("hash-pinned"),
                "{keyword}"
            );
        }
    }

    #[test]
    fn triton_and_helion_need_an_explicit_library() {
        let deck = "PROJECTILE 6\nFISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
        let ctx = ImportContext {
            library: None,
            ..ctx_with_groups(162)
        };
        let error = convert(deck, &ctx).err().unwrap();
        assert!(error.contains("pass --library"), "{error}");
        let conversion = convert(deck, &ctx_with_groups(162)).unwrap();
        assert_eq!(conversion.spec["projectile"], "triton");
        assert!(!entry_detail(&conversion, "PROJECTILE").contains("required"));
    }
}
