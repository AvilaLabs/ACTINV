//! P132a: FISPACT-II input deck -> `actinv-spec-1` problem importer.
//!
//! `actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES] [--library LIB.npz]
//! [--decay DECAY] [--covariance COV.npz] [--fission-yields FILE...] [--damage TABLE]
//! [--self-shielding-table FILE] [--resonance FILE...] [--strict]`
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
//!
//! This module is the pure conversion, deck text to spec and report, with no file, process or
//! network access, so the browser build shares it. File handling and the command line stay in
//! `actinv_cli::fispact_import`, which re-exports everything here.

use crate::flux::parse_fispact_fluxes_text;
use crate::spec::{Spec, POINT_SOURCE_MIN_DISTANCE_M};
use actinv_data::activation::Projectile;
use actinv_data::composition::{material_key, MaterialKey, NA};
use actinv_data::groups::{GroupStructure, NAMED_STRUCTURES};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

/// Upper bound on schedule steps after PULSE expansion.
const MAX_STEPS: usize = 100_000;
/// Deepest PULSE nesting the parser follows.
const MAX_PULSE_DEPTH: usize = 64;
/// Upper bound on command executions after PULSE expansion, steps or not: a loop body that adds
/// no step never reaches `MAX_STEPS`.
const MAX_EXPANDED_COMMANDS: u64 = 2_000_000;
/// Start of the error for a deck that irradiates with no fluxes file.
pub const IRRADIATES_WITHOUT_FLUXES: &str =
    "the deck irradiates (FLUX, POWER or WALL) but no FLUXES file was found";
/// Catalog identifier of the covariance sidecar paired with the default neutron library.
const DEFAULT_COVARIANCE: &str = "tendl-2025-neutron-709g-covariance";
/// Length of the ranked variance-share table that `SENSITIVITY SIGMA` asks for (`uncertainty.voi.top`).
const SENSITIVITY_VOI_TOP: usize = 10;
/// The actinides FISPACT-II lets produce fission products when no FISYIELD narrows or widens the set:
/// U-235, U-238 and Pu-239, as `(ZA, LISO)`.
const DEFAULT_FISSION_PARENTS: [(i32, i32); 3] = [(92235, 0), (92238, 0), (94239, 0)];

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

/// The lines of a deck, ended by `\n`, `\r\n` or a bare `\r` (old Mac and some Windows editors).
fn deck_lines(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut lines = Vec::new();
    let (mut start, mut index) = (0, 0);
    while index < bytes.len() {
        match bytes[index] {
            b'\n' => {
                lines.push(&text[start..index]);
                index += 1;
                start = index;
            }
            b'\r' => {
                lines.push(&text[start..index]);
                index += if bytes.get(index + 1) == Some(&b'\n') {
                    2
                } else {
                    1
                };
                start = index;
            }
            _ => index += 1,
        }
    }
    if start < bytes.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Split a deck into words and `*` text lines. `<< ... >>` comments are removed wherever they
/// occur and may span lines; a line whose first non-blank character is `*` is text, inside which
/// `<<` is not a comment (manual 4.94).
pub fn lex(text: &str) -> Lexed {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut tokens = Vec::new();
    let mut comment_from: Option<usize> = None;
    for (index, raw) in deck_lines(text).into_iter().enumerate() {
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

/// A TIME unit word to the unit string `crate::spec::parse_duration` understands.
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
    ("AINPUT", Arity::Zero),
    ("ALLDISPEN", Arity::Numbers),
    ("ARRAY", Arity::Zero),
    ("ATDISPEN", Arity::Words),
    ("ATOMS", Arity::Zero),
    ("ATWO", Arity::Zero),
    ("BREMSSTRAHLUNG", Arity::Words),
    ("CLEAR", Arity::Zero),
    ("CLOBBER", Arity::Zero),
    ("CNVTYPE", Arity::Numbers),
    ("COLLAPSE", Arity::Zero),
    ("COMPRESSXS", Arity::Numbers),
    ("CONV", Arity::Numbers),
    ("COVARIANCE", Arity::Zero),
    ("CULTAB", Arity::Zero),
    ("CUMFYLD", Arity::Zero),
    ("DENSITY", Arity::Numbers),
    ("DEPLETION", Arity::Words),
    ("DOMINANT", Arity::Numbers),
    ("DOSE", Arity::Numbers),
    ("EAFVERSION", Arity::Numbers),
    ("END", Arity::Special),
    ("ENDPULSE", Arity::Zero),
    ("ENFA", Arity::Numbers),
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
    ("LEVEL", Arity::Numbers),
    ("LIBVERSION", Arity::Numbers),
    ("LIMGRP", Arity::Numbers),
    ("LINA", Arity::Numbers),
    ("LOGLEVEL", Arity::Numbers),
    ("LOOKAHEAD", Arity::Zero),
    ("LOOPS", Arity::Numbers),
    ("MASS", Arity::Special),
    ("MCSAMPLE", Arity::Numbers),
    ("MCSEED", Arity::Numbers),
    ("MIND", Arity::Numbers),
    ("MONITOR", Arity::Numbers),
    ("NEWFILE", Arity::Numbers),
    ("NOCOMP", Arity::Zero),
    ("NOERROR", Arity::Zero),
    ("NOFISS", Arity::Zero),
    ("NOHEAD", Arity::Zero),
    ("NOHEADER", Arity::Zero),
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
    ("SEQNUMBER", Arity::Numbers),
    ("SEQUENTIAL", Arity::Numbers),
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
    ("TAPA", Arity::Numbers),
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
    "AINPUT",
    "ARRAY",
    "ATOMS",
    "ATWO",
    "BREMSSTRAHLUNG",
    "CNVTYPE",
    "COLLAPSE",
    "CUMFYLD",
    "DENSITY",
    "DOSE",
    "END",
    "ENDPULSE",
    "ERROR",
    "FISCHOOSE",
    "FISPACT",
    "FISYIELD",
    "FLUX",
    "FUEL",
    "GETDECAY",
    "GETXS",
    "GRPCONVERT",
    "GROUP",
    "LIMGRP",
    "MASS",
    "MCSAMPLE",
    "MCSEED",
    "MIND",
    "NOERROR",
    "NOFISS",
    "NOSTABLE",
    "OVER",
    "PARTITION",
    "PATH",
    "POWER",
    "PROBTABLE",
    "PROJECTILE",
    "PULSE",
    "RESULT",
    "ROUTES",
    "SENSITIVITY",
    "SORTDOMINANT",
    "SPECTRUM",
    "SSFCHOOSE",
    "SSFDILUTION",
    "SSFFUEL",
    "SSFGEOMETRY",
    "SSFMASS",
    "STEP",
    "TIME",
    "UNCERTAINTY",
    "UNCTYPE",
    "USEFISSION",
    "WALL",
    "ZERO",
];

/// A word of four or more letters that is not an element, a unit or an `OVER` sub-keyword: in a
/// list of nuclides and numbers it can only be a keyword.
fn looks_like_unknown_keyword(word: &str) -> bool {
    word.len() >= 4
        && word
            .chars()
            .all(|character| character.is_ascii_alphabetic())
        && !matches!(
            word.to_ascii_uppercase().as_str(),
            "ACROSS" | "ADCROSS" | "ALAM" | "ADLAM"
        )
        && time_unit(word).is_none()
        && material_key(word).is_err()
}

/// Resolve a deck word to a keyword. FISPACT-II reads the leading characters of a keyword, so
/// `HAZA`, `UNCERT` and `TOL` are the usual spellings: an exact name wins, then a unique name
/// the word is a prefix of (three or more characters), then a unique name sharing the first four
/// characters. `MIN` stays a time unit, not an abbreviation of `MIND`.
fn resolve_keyword(token: &str) -> Option<(&'static str, Arity)> {
    let upper = token.to_ascii_uppercase();
    if let Some(entry) = KEYWORDS.iter().find(|(name, _)| *name == upper) {
        return Some(*entry);
    }
    let length = upper.chars().count();
    if length < 3 || (length == 3 && time_unit(&upper).is_some()) {
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

    /// Every word up to the next keyword. For the keywords whose arguments are numbers, nuclides,
    /// elements and a few sub-keywords (`nuclide_args`), a plain word that is none of these also
    /// ends the list: it is a keyword this importer does not know, and gets its own entry.
    fn take_words(&mut self, nuclide_args: bool) -> Vec<String> {
        let mut out = Vec::new();
        while let Some(token) = self.peek_word() {
            if resolve_keyword(&token.text).is_some()
                || (nuclide_args && looks_like_unknown_keyword(&token.text))
            {
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
                    if depth >= MAX_PULSE_DEPTH {
                        return Err(format!(
                            "line {}: PULSE loops nest deeper than {MAX_PULSE_DEPTH} levels",
                            token.line
                        ));
                    }
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
                        Arity::Words => self.take_words(matches!(
                            name,
                            "OVER" | "PATH" | "ROUTES" | "POWER" | "PARTITION" | "BREMSSTRAHLUNG"
                        )),
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

/// Any other file the command line hashes into the spec (a damage table, a Bondarenko table, an
/// ENDF resonance evaluation).
pub type HashedFile = CovarianceRef;

/// One `--fission-yields` file: its hash and the parent nuclide its MF=8 header names, read by
/// the caller (the conversion itself touches no file).
#[derive(Debug, Clone)]
pub struct FissionYieldFile {
    pub file: HashedFile,
    pub parent_za: i32,
    pub parent_liso: i32,
}

/// Everything the conversion needs from outside the deck.
#[derive(Debug, Clone, Default)]
pub struct ImportContext {
    pub flux: Option<LoadedFlux>,
    pub library: Option<String>,
    pub decay: Option<String>,
    pub covariance: Option<CovarianceRef>,
    /// `--fission-yields`: the ENDF neutron-induced yield files USEFISSION puts in the spec
    pub fission_yields: Vec<FissionYieldFile>,
    /// `--damage`: the damage table ALLDISPEN and ATDISPEN refer to
    pub damage: Option<HashedFile>,
    /// `--self-shielding-table`: the Bondarenko table PROBTABLE and SSFDILUTION refer to
    pub self_shielding_table: Option<HashedFile>,
    /// `--resonance`: the ENDF resonance evaluations SSFGEOMETRY refers to
    pub resonance: Vec<HashedFile>,
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

#[derive(Clone)]
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

#[derive(Clone)]
struct Pending {
    value: f64,
    unit: &'static str,
    line: usize,
    /// what fixed the irradiation level when the TIME was read
    drive: Drive,
    /// the TIME's report entry, which says so when the interval never runs
    entry: Option<usize>,
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

#[derive(Clone)]
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
#[derive(Default, Clone)]
struct PhotonRec {
    group_structure: Option<&'static str>,
    point_source_m: Option<f64>,
    bremsstrahlung: Vec<String>,
}

/// GRPCONVERT, CNVTYPE and LIMGRP, each with the report entry that `finish` settles.
#[derive(Default, Clone)]
struct RebinRec {
    grpconvert: Option<(Vec<f64>, Option<usize>)>,
    cnvtype: Option<(u8, Option<usize>)>,
    limgrp: Option<(f64, f64, Option<usize>)>,
}

/// The report entries of a keyword that `finish` settles once the whole deck is known; `None` until
/// the keyword is read. A repeat adds its entry to the list (a loop body logs only once).
type Seen = Option<Vec<usize>>;

fn mark_seen(slot: &mut Seen, entry: Option<usize>) {
    slot.get_or_insert_with(Vec::new).extend(entry);
}

/// A FISYIELD or FISCHOOSE nuclide list: the declared count and the nuclides as `(name, ZA, LISO)`.
type NuclideList = (i64, Vec<(String, i32, i32)>);

/// The deck's fission keywords, settled in `finish` against the `--fission-yields` files.
#[derive(Default, Clone)]
struct FissionRec {
    use_fission: Seen,
    nofiss: Seen,
    cumulative: Seen,
    /// FISYIELD: the last list read, with its entries
    yield_parents: Option<(NuclideList, Vec<usize>)>,
    /// FISCHOOSE: the last list read, with its entries
    choose: Option<(Vec<String>, Vec<usize>)>,
}

/// SSFGEOMETRY's target: shape name, length and cylinder height in cm.
#[derive(Clone)]
struct GeometryRec {
    shape: &'static str,
    length_cm: f64,
    height_cm: Option<f64>,
}

/// The deck's self-shielding keywords, settled in `finish` against `--resonance` and
/// `--self-shielding-table`.
#[derive(Default, Clone)]
struct ShieldRec {
    geometry: Option<(GeometryRec, Vec<usize>)>,
    probtable: Seen,
    /// SSFCHOOSE: element or nuclide names
    choose: Option<(Vec<String>, Vec<usize>)>,
    mass: Seen,
    fuel: Seen,
    /// SSFDILUTION: nuclide -> FISPACT-II group number (1 = highest energy) -> dilution in barns
    dilution: Option<(DilutionRows, Vec<usize>)>,
}

/// SSFDILUTION rows: nuclide -> FISPACT-II group number -> dilution in barns.
type DilutionRows = BTreeMap<String, BTreeMap<u64, f64>>;

/// The deck's uncertainty-side keywords, settled in `finish` once the uncertainty block is known.
#[derive(Default, Clone)]
struct UncertaintyRec {
    /// UNCTYPE: 1 cross sections, 2 half-lives, 3 both; the last one read applies
    unctype: Option<(i64, Vec<usize>)>,
    /// SENSITIVITY SIGMA
    sensitivity: Seen,
    /// MCSAMPLE and MCSEED
    sampling: Vec<usize>,
    /// ERROR entries and the cross-section uncertainty overrides they ask for
    error_entries: Vec<usize>,
    error_overrides: Vec<OverrideRec>,
}

#[derive(Clone)]
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
    /// the report entry of the DOSE 2 that set `photon.point_source_m`
    dose_entry: Option<usize>,
    rebin: RebinRec,
    fission: FissionRec,
    shield: ShieldRec,
    unc: UncertaintyRec,
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

/// Placeholder detail of a deferred keyword's entry until `finish` settles it.
const DEFERRED_DETAIL: &str = "resolved when the deck ends";

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
        "NOHEAD" | "NOHEADER" => (Noted, "output header suppression; ACTINV's result JSON has no header"),
        "CONV" | "DOMINANT" | "EAFVERSION" | "ENFA" | "LEVEL" | "LINA" | "LOOPS" | "NEWFILE" | "SEQNUMBER"
        | "SEQUENTIAL" | "TAPA" => (
            Noted,
            "legacy FISPACT-II print, format or file-handling switch; no effect on the calculated physics",
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
        "COVARIANCE" => (
            Noted,
            "FISPACT-II's collapse of the cross-section covariances; ACTINV propagates the full MF=33 covariance of the library's own groups, so there is no collapse step to switch on",
        ),
        "XSTHRESHOLD" => (
            Noted,
            "the smallest cross section FISPACT-II admits to its pathway-based uncertainty estimate, a cost control of that method; ACTINV propagates the full covariance without pathway truncation",
        ),
        "DEPLETION" => (
            Noted,
            "an output request for the depletion of named nuclides; every result step carries the atoms of every nuclide, and the spec's summary.depletion adds the per-step depletion table with its decay and reaction channels",
        ),
        "IRON" => (
            Unsupported,
            "not a keyword of the 2023 FISPACT-II manual (it has no section), so its behaviour is not defined and ACTINV has no equivalent",
        ),
        "NOXRAY" | "READGG" => (
            Unsupported,
            "library-data selection or modification keyword with no ACTINV equivalent",
        ),
        "READSF" => (
            Unsupported,
            "spontaneous-fission yields read from a library directory; ACTINV takes them from spec fission_yields.spontaneous (hash-pinned ENDF sfy files and a nu-bar source), which the importer has no option to fill: add the section by hand",
        ),
        "USESPALLATION" => (
            Unsupported,
            "splices spallation cross sections above a cutoff energy into the library; ACTINV reads one activation library and has no spallation stream",
        ),
        "USEXSEXTRA" => (
            Unsupported,
            "prefers an extra cross-section stream over the main library; ACTINV reads one activation library, so build the combined library and pass it with --library",
        ),
        _ => return None,
    })
}

fn drive_text(drive: Drive) -> String {
    match drive {
        Drive::Flux(value) => format!("flux {value:e} cm^-2 s^-1"),
        Drive::Power(value) => format!("power {value:e} W cm^-3"),
        Drive::Wall(value) => format!("wall loading {value:e} MW m^-2"),
    }
}

/// The report text of a TIME that no output keyword ran: it is not in the schedule.
fn lost_time_detail(lost: &Pending, replaced_on: Option<usize>) -> String {
    let cause = match replaced_on {
        Some(line) => {
            format!("the TIME on line {line} replaced it before an ATOMS, STEP or SPECTRUM ran it")
        }
        None => "no ATOMS, STEP or SPECTRUM follows it".to_string(),
    };
    format!(
        "this interval never ran ({cause}) and is missing from the schedule: {} {} at {}. A TIME runs at the next ATOMS, STEP or SPECTRUM; put one after it if the interval should run",
        lost.value,
        lost.unit,
        drive_text(lost.drive)
    )
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

/// A nuclide's spec name from its `(ZA, LISO)`: `U235`, `Am242m1`.
fn nuclide_label(za: i32, liso: i32) -> String {
    let symbol = actinv_data::composition::symbol_of(za / 1000);
    if liso > 0 {
        format!("{symbol}{}m{liso}", za % 1000)
    } else {
        format!("{symbol}{}", za % 1000)
    }
}

/// `fission_yields.parents` for the yield files supplied (their parents as `(ZA, LISO)`) and the
/// deck's FISYIELD list (`None`: the FISPACT-II default of U-235, U-238 and Pu-239), with the text
/// that says what was chosen. A parent without a yield file does not fission in FISPACT-II, so a
/// named parent that has no supplied file is dropped and reported; an include list left empty
/// excludes every supplied parent instead, since the spec has no empty include.
fn fission_parent_selection(
    supplied: &[(i32, i32)],
    list: Option<&NuclideList>,
) -> (Option<Value>, String) {
    let label = |keys: &[(i32, i32)]| -> Vec<String> {
        keys.iter()
            .map(|(za, liso)| nuclide_label(*za, *liso))
            .collect()
    };
    let exclude_all = || json!({"exclude": label(supplied)});
    type Keys = Vec<(i32, i32)>;
    let split = |names: Keys| -> (Keys, Keys) {
        let mut seen = std::collections::BTreeSet::new();
        names
            .into_iter()
            .filter(|key| seen.insert(*key))
            .partition(|key| supplied.contains(key))
    };
    let dropped_text = |dropped: &[(i32, i32)]| -> String {
        if dropped.is_empty() {
            String::new()
        } else {
            format!(
                "; dropped, no yield file supplied for {}",
                label(dropped).join(", ")
            )
        }
    };
    match list {
        Some((0, _)) => (
            Some(exclude_all()),
            "FISYIELD 0: every parent with a yield file is excluded, so no fission products are produced"
                .to_string(),
        ),
        Some((count, nuclides)) => {
            let (kept, dropped) =
                split(nuclides.iter().map(|(_, za, liso)| (*za, *liso)).collect());
            let dropped = dropped_text(&dropped);
            if *count > 0 && kept.is_empty() {
                (
                    Some(exclude_all()),
                    format!("FISYIELD {count}: none of the named parents has a yield file, so no parent produces fission products{dropped}"),
                )
            } else if *count > 0 {
                (
                    Some(json!({"include": label(&kept)})),
                    format!("FISYIELD {count}: parents.include = {}{dropped}", label(&kept).join(", ")),
                )
            } else if kept.is_empty() {
                (
                    None,
                    format!("FISYIELD {count}: none of the excluded parents has a yield file, so every parent with one produces fission products{dropped}"),
                )
            } else {
                (
                    Some(json!({"exclude": label(&kept)})),
                    format!("FISYIELD {count}: parents.exclude = {}{dropped}", label(&kept).join(", ")),
                )
            }
        }
        None => {
            let (kept, dropped) = split(DEFAULT_FISSION_PARENTS.to_vec());
            let dropped = dropped_text(&dropped);
            if kept.is_empty() {
                (
                    Some(exclude_all()),
                    format!("no FISYIELD: the default parents (U235, U238, Pu239) have no yield file, so no parent produces fission products{dropped}"),
                )
            } else {
                (
                    Some(json!({"include": label(&kept)})),
                    format!("no FISYIELD: the default parents, parents.include = {}{dropped}", label(&kept).join(", ")),
                )
            }
        }
    }
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
            dose_entry: None,
            rebin: RebinRec::default(),
            fission: FissionRec::default(),
            shield: ShieldRec::default(),
            unc: UncertaintyRec::default(),
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
                        "zero repetitions: the loop body does not run; its keywords are listed so that nothing is dropped silently, and have no effect".to_string()
                    } else {
                        format!("loop body expanded {count} times into explicit schedule steps")
                    };
                    self.log(cmd, Status::Mapped, detail);
                    if *count == 0 && record {
                        self.report_skipped_body(cmd, body);
                    }
                    for repeat in 0..*count {
                        // keywords inside the loop are reported once, not once per repetition
                        self.run_nodes(body, record && repeat == 0)?;
                    }
                }
            }
        }
        Ok(())
    }

    /// The keywords of a `PULSE 0` body get report entries, but the body changes nothing: it is
    /// interpreted once, and every effect on the schedule and the spec is undone.
    fn report_skipped_body(&mut self, cmd: &Command, body: &[Node]) {
        let saved = self.clone();
        let before = self.entries.len();
        let outcome = self.run_nodes(body, true);
        let mut added = self.entries.split_off(before);
        *self = saved;
        for entry in &mut added {
            if entry.detail == DEFERRED_DETAIL {
                // a deferred keyword is settled when the deck ends, which a skipped body never reaches
                entry.detail = "inside a PULSE 0 body: skipped, no effect on the problem".into();
            } else {
                entry
                    .detail
                    .push_str(" (inside a PULSE 0 body: skipped, no effect on the problem)");
            }
        }
        self.entries.extend(added);
        if let Err(reason) = outcome {
            self.recording = true;
            self.log(
                cmd,
                Status::Unsupported,
                format!("the skipped loop body is not valid: {reason}"),
            );
        }
    }

    fn library_is_explicit(&self) -> bool {
        self.ctx.library.is_some()
    }

    fn apply(&mut self, cmd: &Command) -> Result<(), String> {
        if !cmd.known {
            self.log(
                cmd,
                Status::Unsupported,
                "unknown to this importer (a misspelling, a keyword from another FISPACT-II release, or one it does not cover); it cannot tell what it was meant to do",
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
                let replaced = self.pending.take();
                let mut detail =
                    format!("interval {value} {unit}; runs at the next ATOMS, STEP or SPECTRUM");
                if let Some(old) = &replaced {
                    detail.push_str(&format!(
                        " (replaces the interval from line {}, which no output keyword ran)",
                        old.line
                    ));
                }
                let entry = self.log(cmd, Status::Mapped, detail);
                if let Some(old) = replaced {
                    self.set_entry(
                        old.entry,
                        Status::Unsupported,
                        lost_time_detail(&old, Some(cmd.line)),
                    );
                }
                self.pending = Some(Pending {
                    value,
                    unit,
                    line: cmd.line,
                    drive: self.current,
                    entry,
                });
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
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                self.uncert_entry = self.replace_index(self.uncert_entry, index, cmd.line);
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
            "GETXS" => self.apply_getxs(cmd)?,
            "GETDECAY" => self.apply_getdecay(cmd),
            // the deprecated spellings: COLLAPSE is GETXS 1, ARRAY is GETDECAY 0, AINPUT both at 0
            "COLLAPSE" => {
                let getxs = Command {
                    args: vec!["1".to_string()],
                    ..cmd.clone()
                };
                self.apply_getxs(&getxs)?;
            }
            "ARRAY" => self.apply_getdecay(cmd),
            "AINPUT" => {
                let getxs = Command {
                    args: vec!["0".to_string()],
                    ..cmd.clone()
                };
                self.apply_getxs(&getxs)?;
                self.apply_getdecay(cmd);
            }
            "USEFISSION" | "NOFISS" | "CUMFYLD" => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                let slot = match cmd.keyword.as_str() {
                    "USEFISSION" => &mut self.fission.use_fission,
                    "NOFISS" => &mut self.fission.nofiss,
                    _ => &mut self.fission.cumulative,
                };
                mark_seen(slot, index);
            }
            "FISYIELD" | "FISCHOOSE" => self.apply_fission_list(cmd),
            "UNCTYPE" => self.apply_unctype(cmd)?,
            "SENSITIVITY" => self.apply_sensitivity(cmd),
            "MCSAMPLE" | "MCSEED" => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                self.unc.sampling.extend(index);
            }
            "ERROR" => self.apply_error(cmd),
            "PROBTABLE" => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                mark_seen(&mut self.shield.probtable, index);
            }
            "SSFGEOMETRY" => self.apply_ssf_geometry(cmd),
            "SSFCHOOSE" => self.apply_ssf_choose(cmd),
            "SSFMASS" | "SSFFUEL" => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                let slot = if cmd.keyword == "SSFMASS" {
                    &mut self.shield.mass
                } else {
                    &mut self.shield.fuel
                };
                mark_seen(slot, index);
            }
            "SSFDILUTION" => self.apply_ssf_dilution(cmd),
            "RESULT" => {
                // RESULT stands in for an inventory run that precedes a pathway calculation
                if self.material.is_some() && (!self.steps.is_empty() || self.pending.is_some()) {
                    self.log(
                        cmd,
                        Status::Noted,
                        "the deck defines and runs its own inventory (material, then a schedule step), and ACTINV's pathways are built from that run; the atom counts listed here are not used",
                    );
                } else {
                    self.log(
                        cmd,
                        Status::Unsupported,
                        "no inventory run (material and a schedule step) precedes it, so the atom counts it lists would be the only inventory; ACTINV starts every problem from the material and its schedule and cannot take an inventory as a pathway input",
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

    /// FISYIELD and FISCHOOSE: a count and that many nuclide names (FISYIELD's count is signed).
    fn apply_fission_list(&mut self, cmd: &Command) {
        let signed = cmd.keyword == "FISYIELD";
        let list = (|| -> Result<NuclideList, String> {
            let count = arg_whole(cmd, 0, "nuclide count")?;
            if count < 0 && !signed {
                return Err(format!(
                    "line {}: {} count must not be negative",
                    cmd.line, cmd.keyword
                ));
            }
            let names = &cmd.args[1..];
            if names.len() as u64 != count.unsigned_abs() {
                return Err(format!(
                    "line {}: {} declares {} nuclide(s) but lists {}",
                    cmd.line,
                    cmd.keyword,
                    count.unsigned_abs(),
                    names.len()
                ));
            }
            let mut nuclides = Vec::new();
            for raw in names {
                nuclides.push(fispact_nuclide(raw).map_err(|reason| {
                    format!("line {}: {} nuclide {reason}", cmd.line, cmd.keyword)
                })?);
            }
            Ok((count, nuclides))
        })();
        let list = match list {
            Ok(list) => list,
            Err(reason) => {
                self.log(cmd, Status::Unsupported, reason);
                return;
            }
        };
        let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
        if signed {
            let mut entries = self
                .fission
                .yield_parents
                .take()
                .map(|(_, entries)| entries)
                .unwrap_or_default();
            entries.extend(index);
            self.fission.yield_parents = Some((list, entries));
        } else {
            let names = list.1.into_iter().map(|(name, ..)| name).collect();
            let mut entries = self
                .fission
                .choose
                .take()
                .map(|(_, entries)| entries)
                .unwrap_or_default();
            entries.extend(index);
            self.fission.choose = Some((names, entries));
        }
    }

    /// UNCTYPE: which uncertainty contributions to include (1 cross sections, 2 half-lives, 3 both).
    fn apply_unctype(&mut self, cmd: &Command) -> Result<(), String> {
        let kind = if cmd.args.is_empty() {
            1
        } else {
            arg_whole(cmd, 0, "contribution type")?
        };
        if !(1..=3).contains(&kind) {
            self.log(
                cmd,
                Status::Unsupported,
                format!("UNCTYPE {kind}: the contribution type is 1 (cross sections), 2 (half-lives) or 3 (both)"),
            );
            return Ok(());
        }
        let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
        let mut entries = self
            .unc
            .unctype
            .take()
            .map(|(_, entries)| entries)
            .unwrap_or_default();
        entries.extend(index);
        self.unc.unctype = Some((kind, entries));
        Ok(())
    }

    /// SENSITIVITY: SIGMA (cross-section sensitivities) maps to the ranked variance shares; LAMBDA
    /// (decay-constant sensitivities) has no equivalent.
    fn apply_sensitivity(&mut self, cmd: &Command) {
        let mode = cmd
            .args
            .first()
            .map(|word| word.to_ascii_uppercase())
            .unwrap_or_default();
        match mode.as_str() {
            "SIGMA" | "SIGM" | "SIG" => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                mark_seen(&mut self.unc.sensitivity, index);
            }
            "LAMBDA" | "LAMB" | "LAM" => {
                self.log(
                    cmd,
                    Status::Unsupported,
                    "sensitivity coefficients with respect to decay constants: ACTINV ranks no decay-constant sensitivities (uncertainty.channels decay_constants propagates the half-life uncertainties but gives no per-parameter ranking), and the FISPACT-II manual marks the option as unavailable in its current version",
                );
            }
            _ => {
                self.log(
                    cmd,
                    Status::Unsupported,
                    "SENSITIVITY takes SIGMA or LAMBDA as its first word",
                );
            }
        }
    }

    /// ERROR: the fractional error of named parent -> daughter reactions. A non-negative value
    /// becomes a `cross_section_uncertainty` override, settled in `finish` with the uncertainty
    /// block it needs; -1 asks for the library's own value.
    fn apply_error(&mut self, cmd: &Command) {
        let words = &cmd.args;
        let Some(count) = words
            .first()
            .and_then(|raw| parse_number(raw))
            .filter(|value| *value >= 0.0 && value.fract() == 0.0 && *value <= 1.0e6)
        else {
            self.log(
                cmd,
                Status::Unsupported,
                "ERROR needs the number of reactions first",
            );
            return;
        };
        let count = count as usize;
        if words.len() != 1 + 3 * count {
            self.log(
                cmd,
                Status::Unsupported,
                format!(
                    "ERROR declares {count} reaction(s), which take {} words (parent, daughter, error), but lists {}",
                    3 * count,
                    words.len() - 1
                ),
            );
            return;
        }
        if !self.projectile.is_neutron() {
            self.log(
                cmd,
                Status::Unsupported,
                "the daughter-to-ENDF-reaction mapping is defined for neutron projectiles only",
            );
            return;
        }
        let (mut library, mut asked) = (0usize, 0usize);
        let mut failures: Vec<String> = Vec::new();
        for triple in words[1..].chunks(3) {
            let outcome = (|| -> Result<Option<OverrideRec>, String> {
                let (nuclide, za, _) =
                    fispact_nuclide(&triple[0]).map_err(|reason| format!("parent {reason}"))?;
                let mt = if triple[1].eq_ignore_ascii_case("FISSION") || triple[1] == "0" {
                    18
                } else {
                    let (_, daughter_za, _) = fispact_nuclide(&triple[1])
                        .map_err(|reason| format!("daughter {reason}"))?;
                    reaction_mt(za, daughter_za)?
                };
                let error = parse_number(&triple[2])
                    .ok_or_else(|| format!("the error '{}' is not a number", triple[2]))?;
                if error == -1.0 {
                    return Ok(None);
                }
                if error < 0.0 {
                    return Err(format!(
                        "the error {error} is negative; -1 asks for the library value"
                    ));
                }
                Ok(Some(OverrideRec {
                    nuclide,
                    kind: "cross_section_uncertainty",
                    mt: Some(mt),
                    value: error,
                    units: None,
                }))
            })();
            match outcome {
                Ok(None) => library += 1,
                Ok(Some(record)) => {
                    let known = self
                        .unc
                        .error_overrides
                        .iter()
                        .find(|known| known.nuclide == record.nuclide && known.mt == record.mt)
                        .cloned();
                    match known {
                        Some(known) if known == record => asked += 1,
                        Some(known) => failures.push(format!(
                            "{} MT {} was already set to {}",
                            record.nuclide,
                            record.mt.unwrap_or_default(),
                            known.value
                        )),
                        None => {
                            self.unc.error_overrides.push(record);
                            asked += 1;
                        }
                    }
                }
                Err(reason) => failures.push(format!("{} {}: {reason}", triple[0], triple[1])),
            }
        }
        if !failures.is_empty() {
            self.log(
                cmd,
                Status::Unsupported,
                format!(
                    "{} reaction(s) cannot be read: {}",
                    failures.len(),
                    failures.join("; ")
                ),
            );
        } else if asked == 0 {
            self.log(
                cmd,
                Status::Noted,
                format!("{library} reaction(s) ask for the library's own uncertainty (-1), which is what ACTINV's covariance supplies"),
            );
        } else {
            let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
            self.unc.error_entries.extend(index);
        }
    }

    /// SSFGEOMETRY type length1 [length2]: the target shape of the geometry self-shielding.
    fn apply_ssf_geometry(&mut self, cmd: &Command) {
        let parsed = (|| -> Result<GeometryRec, String> {
            let kind = arg_whole(cmd, 0, "target type")?;
            let length = arg_number(cmd, 1, "length")?;
            let (shape, wants_height) = match kind {
                1 => ("foil", false),
                2 => ("wire", false),
                3 => ("sphere", false),
                4 => ("cylinder", true),
                other => {
                    return Err(format!(
                        "target type {other} is not one ACTINV has: 1 foil, 2 wire, 3 sphere, 4 cylinder"
                    ))
                }
            };
            let height = if wants_height {
                Some(arg_number(cmd, 2, "cylinder height")?)
            } else {
                None
            };
            let positive = |value: f64| value.is_finite() && value > 0.0;
            if !positive(length) || height.is_some_and(|value| !positive(value)) {
                return Err("the lengths must be positive".into());
            }
            Ok(GeometryRec {
                shape,
                length_cm: length,
                height_cm: height,
            })
        })();
        match parsed {
            Ok(record) => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                let mut entries = self
                    .shield
                    .geometry
                    .take()
                    .map(|(_, entries)| entries)
                    .unwrap_or_default();
                entries.extend(index);
                self.shield.geometry = Some((record, entries));
            }
            Err(reason) => {
                self.log(cmd, Status::Unsupported, reason);
            }
        }
    }

    /// SSFCHOOSE ncho [nprint], then ncho element or nuclide names. The print switch only changes
    /// FISPACT-II's output and may be left out, so the count of words after `ncho` decides.
    fn apply_ssf_choose(&mut self, cmd: &Command) {
        let parsed = (|| -> Result<Vec<String>, String> {
            let count = arg_whole(cmd, 0, "name count")?;
            let rest = &cmd.args[1..];
            let names = match usize::try_from(count) {
                Ok(count) if rest.len() == count => rest,
                Ok(count) if rest.len() == count + 1 => &rest[1..],
                _ => {
                    return Err(format!(
                        "SSFCHOOSE declares {count} name(s) but lists {}",
                        rest.len()
                    ))
                }
            };
            names
                .iter()
                .map(|raw| match material_key(raw) {
                    Ok(MaterialKey::Nuclide { canonical, .. }) => Ok(canonical),
                    Ok(MaterialKey::Element(element)) => Ok(element),
                    Err(error) => Err(format!("'{raw}': {error}")),
                })
                .collect()
        })();
        match parsed {
            Ok(names) => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                let mut entries = self
                    .shield
                    .choose
                    .take()
                    .map(|(_, entries)| entries)
                    .unwrap_or_default();
                entries.extend(index);
                self.shield.choose = Some((names, entries));
            }
            Err(reason) => {
                self.log(cmd, Status::Unsupported, reason);
            }
        }
    }

    /// SSFDILUTION nnuc, then per nuclide its name, a count and that many group/dilution pairs.
    fn apply_ssf_dilution(&mut self, cmd: &Command) {
        let parsed = (|| -> Result<BTreeMap<String, BTreeMap<u64, f64>>, String> {
            let words = &cmd.args;
            let nnuc = arg_whole(cmd, 0, "nuclide count")?;
            let mut table: BTreeMap<String, BTreeMap<u64, f64>> = BTreeMap::new();
            let mut at = 1usize;
            for _ in 0..nnuc.max(0) {
                let name = words
                    .get(at)
                    .ok_or("fewer nuclides than the count declares")?;
                let (canonical, ..) = fispact_nuclide(name)?;
                let pairs = words
                    .get(at + 1)
                    .and_then(|raw| parse_number(raw))
                    .filter(|value| *value >= 1.0 && value.fract() == 0.0 && *value <= 1.0e6)
                    .ok_or_else(|| format!("{name} needs a whole-number count of group entries"))?
                    as usize;
                at += 2;
                let rows = table.entry(canonical).or_default();
                for _ in 0..pairs {
                    let group = words
                        .get(at)
                        .and_then(|raw| parse_number(raw))
                        .filter(|value| *value >= 1.0 && value.fract() == 0.0)
                        .ok_or_else(|| format!("{name}: a group number is a whole number from 1"))?
                        as u64;
                    let dilution = words
                        .get(at + 1)
                        .and_then(|raw| parse_number(raw))
                        .filter(|value| *value > 0.0)
                        .ok_or_else(|| {
                            format!("{name} group {group}: the dilution must be positive barns")
                        })?;
                    rows.insert(group, dilution);
                    at += 2;
                }
            }
            if at != words.len() {
                return Err(format!(
                    "{} word(s) follow the declared entries",
                    words.len() - at
                ));
            }
            Ok(table)
        })();
        match parsed {
            Ok(table) => {
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                let (mut merged, mut entries) = self.shield.dilution.take().unwrap_or_default();
                for (name, rows) in table {
                    merged.entry(name).or_default().extend(rows);
                }
                entries.extend(index);
                self.shield.dilution = Some((merged, entries));
            }
            Err(reason) => {
                self.log(cmd, Status::Unsupported, reason);
            }
        }
    }

    /// GETXS (and the deprecated COLLAPSE, AINPUT).
    fn apply_getxs(&mut self, cmd: &Command) -> Result<(), String> {
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
        Ok(())
    }

    /// GETDECAY (and the deprecated ARRAY, AINPUT).
    fn apply_getdecay(&mut self, cmd: &Command) {
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
        // POWER p n mt_1 .. mt_n: p in W/cc, normalized on the kerma of the n listed MTs
        let declared = cmd
            .args
            .get(1)
            .and_then(|word| parse_number(word))
            .filter(|value| value.fract() == 0.0 && (0.0..=1.0e6).contains(value))
            .map(|value| value as usize);
        let rest = cmd.args.get(2..).unwrap_or_default();
        let no_words: &[String] = &[];
        let (mts, extra): (&[String], &[String]) = match declared {
            Some(count) => rest.split_at(count.min(rest.len())),
            None => (no_words, cmd.args.get(1..).unwrap_or_default()),
        };
        let mut detail = format!(
            "steps that follow take power_W_cm3 = {power} (FISPACT-II's W/cc) with material.density_g_cm3 from DENSITY; the flux amplitude is solved from kerma heating, which needs a hash-pinned kerma.table that no deck provides (see required_additions)"
        );
        if mts.is_empty() {
            detail.push_str("; the deck names no MT list, and ACTINV normalizes on the table's total kerma (MT=301)");
        } else if mts.len() != 1 || mts[0] != "301" {
            detail.push_str(&format!(
                "; the deck normalizes on MT list '{}', ACTINV on the table's total kerma (MT=301)",
                mts.join(" ")
            ));
        }
        if let Some(count) = declared.filter(|count| *count > mts.len()) {
            detail.push_str(&format!(
                "; the deck declares {count} MT number(s) but lists {}",
                mts.len()
            ));
        }
        if !extra.is_empty() {
            detail.push_str(&format!(
                "; the argument(s) '{}' after the MT list are not interpreted",
                extra.join(" ")
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
        // overrides are not step-scoped in the spec, FISPACT-II applies OVER from this point on
        let late = cmd.phase == Phase::Inventory && !self.steps.is_empty();
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
            let (mut status, mut detail) = outcome;
            if late && status != Status::Unsupported {
                status = Status::Approximated;
                detail.push_str("; applies to the whole run, including the steps already scheduled, because ACTINV overrides are not step-scoped (FISPACT-II applies OVER from this point)");
            }
            match status {
                Status::Mapped => mapped += 1,
                Status::Unsupported => unsupported += 1,
                _ => approximated += 1,
            }
            self.sub_entry(cmd, &sub, shown, status, detail);
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
        // not `..parent.clone()`: the parent's argument list can hold thousands of words
        let sub = Command {
            keyword: keyword.to_string(),
            known: parent.known,
            line: parent.line,
            phase: parent.phase,
            args: if args.is_empty() {
                Vec::new()
            } else {
                vec![args]
            },
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
                let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                let previous = self.rebin.grpconvert.as_ref().and_then(|(_, index)| *index);
                let index = self.replace_index(previous, index, cmd.line);
                self.rebin.grpconvert = Some((numbers, index));
            }
            "CNVTYPE" => match numbers.first() {
                Some(code) if *code == 0.0 || *code == 1.0 => {
                    let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                    let previous = self.rebin.cnvtype.and_then(|(_, index)| index);
                    let index = self.replace_index(previous, index, cmd.line);
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
                    let index = self.log(cmd, Status::Noted, DEFERRED_DETAIL);
                    let previous = self.rebin.limgrp.and_then(|(_, _, index)| index);
                    let index = self.replace_index(previous, index, cmd.line);
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
        let late = cmd.phase == Phase::Inventory && !self.steps.is_empty();
        match mode {
            1 if late && self.photon.point_source_m.is_some() => {
                self.log(
                    cmd,
                    Status::Approximated,
                    "back to the semi-infinite slab contact dose after steps were scheduled: the spec carries one dose geometry for the whole run, so the earlier DOSE 2 point source stays",
                );
            }
            1 => {
                if self.photon.point_source_m.take().is_some() {
                    let previous = self.dose_entry.take();
                    self.set_entry(
                        previous,
                        Status::Noted,
                        format!(
                            "superseded by the DOSE 1 on line {} before any step ran: the contact dose applies",
                            cmd.line
                        ),
                    );
                }
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
                    let (status, detail) = if used > distance {
                        (
                            Status::Approximated,
                            format!("photon.point_source.distance_m = {used}: FISPACT-II raises a distance below {POINT_SOURCE_MIN_DISTANCE_M} m to that minimum, and so does this importer (ACTINV itself rejects it)"),
                        )
                    } else if late {
                        (
                            Status::Approximated,
                            format!("photon.point_source.distance_m = {used} applies to the whole run; FISPACT-II applied it from this point"),
                        )
                    } else {
                        (
                            Status::Mapped,
                            format!("photon.point_source.distance_m = {used}; the dose values are null unless photon.response (with an air_mass_attenuation curve) is added"),
                        )
                    };
                    let index = self.log(cmd, status, detail);
                    if let Some(index) = index {
                        // a later DOSE 2 replaces the distance of the earlier one
                        let previous = self.dose_entry.replace(index);
                        self.set_entry(
                            previous,
                            if late {
                                Status::Approximated
                            } else {
                                Status::Noted
                            },
                            format!(
                                "superseded by the DOSE 2 on line {}: the last distance applies to the whole run",
                                cmd.line
                            ),
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

    /// The index a deferred keyword settles at the end of the deck: a new request supersedes the
    /// entry of the one before it. Repetitions of a loop body log nothing and keep the index.
    fn replace_index(
        &mut self,
        previous: Option<usize>,
        new: Option<usize>,
        line: usize,
    ) -> Option<usize> {
        if new.is_none() {
            return previous;
        }
        self.set_entry(
            previous,
            Status::Noted,
            format!("superseded by the request on line {line}: the last one applies"),
        );
        new
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

    /// The covariance sidecar an uncertainty block uses, and whether the command line named it: the
    /// `--covariance` file, else the catalog sidecar of the default library, else none.
    fn covariance_source(
        &self,
        defaulted_library: bool,
    ) -> Result<Option<(CovarianceRef, bool)>, String> {
        Ok(match &self.ctx.covariance {
            Some(reference) => Some((reference.clone(), true)),
            None if defaulted_library => {
                let catalog = actinv_data::catalog::embedded_catalog()?;
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
        })
    }

    /// The same status and text on every entry of a keyword.
    fn settle(&mut self, entries: &[usize], status: Status, detail: impl Into<String>) {
        let detail = detail.into();
        for index in entries {
            self.set_entry(Some(*index), status, detail.clone());
        }
    }

    /// Settle the uncertainty keywords against the uncertainty block (`None` when none is emitted):
    /// SENSITIVITY SIGMA, UNCTYPE, ERROR, MCSAMPLE and MCSEED. An ERROR override joins `overrides`
    /// only beside a block that carries the cross-section channel it rescales.
    fn settle_uncertainty_keywords(
        &mut self,
        uncertainty: &mut Option<Value>,
        blocker: Option<&str>,
        defaulted_library: bool,
    ) -> Result<(), String> {
        let mut sensitivity_mapped = false;
        if let Some(entries) = self.unc.sensitivity.take() {
            let mut refusal: Option<String> = None;
            if uncertainty.is_none() {
                refusal = if self.noerror {
                    Some("NOERROR switches uncertainty off".to_string())
                } else if !self.projectile.is_neutron() {
                    Some("MF=33 uncertainty is available for neutron activation only".to_string())
                } else if let Some(blocker) = blocker {
                    Some(format!("ACTINV refuses uncertainty beside {blocker}"))
                } else {
                    match self.covariance_source(defaulted_library)? {
                        Some((reference, _)) => {
                            *uncertainty = Some(json!({
                                "covariance": {"path": reference.path, "sha256": reference.sha256},
                            }));
                            None
                        }
                        None => Some(
                            "it needs a covariance sidecar paired with the library: pass --covariance FILE"
                                .to_string(),
                        ),
                    }
                };
            }
            match (refusal, uncertainty.as_mut()) {
                (None, Some(block)) => {
                    block["voi"] = json!({"top": SENSITIVITY_VOI_TOP});
                    sensitivity_mapped = true;
                    self.settle(
                        &entries,
                        Status::Approximated,
                        format!("uncertainty.voi.top = {SENSITIVITY_VOI_TOP}: ACTINV ranks the cross-section parameters by first-order variance share of each response, in place of FISPACT-II's sampled sensitivity coefficients; the reactions, nuclides and correlation cut-off the deck lists do not restrict the table, and the uncertainty block is added even when no UNCERTAINTY keyword asked for one"),
                    );
                }
                (refusal, _) => self.settle(
                    &entries,
                    Status::Unsupported,
                    format!(
                        "no uncertainty block can be emitted, so there are no variance shares: {}",
                        refusal.unwrap_or_default()
                    ),
                ),
            }
        }
        let sampling = std::mem::take(&mut self.unc.sampling);
        if sensitivity_mapped {
            self.settle(
                &sampling,
                Status::Noted,
                "parameterises the Monte-Carlo sampling of SENSITIVITY (distribution, sample count, cut-offs or random seed); the mapped sensitivity is ACTINV's analytic first-order result, so none of them has an effect",
            );
        } else {
            self.settle(
                &sampling,
                Status::Unsupported,
                "parameterises the Monte-Carlo sampling of SENSITIVITY, and no SENSITIVITY SIGMA was mapped; ACTINV has no sampling to configure",
            );
        }
        let mut channel_two_only = false;
        if let Some((kind, entries)) = self.unc.unctype.take() {
            match uncertainty.as_mut() {
                Some(block) => {
                    let (channels, status, detail) = match kind {
                        1 => (
                            vec!["cross_section_mf33"],
                            Status::Mapped,
                            "uncertainty.channels = [cross_section_mf33]",
                        ),
                        2 => {
                            channel_two_only = true;
                            (
                                vec!["decay_constants"],
                                Status::Approximated,
                                "uncertainty.channels = [decay_constants]; ACTINV propagates the MF=33 covariance whenever a covariance sidecar is given, so the cross-section contribution cannot be switched off and the band is wider than the half-life-only band FISPACT-II reports",
                            )
                        }
                        _ => (
                            vec!["cross_section_mf33", "decay_constants"],
                            Status::Mapped,
                            "uncertainty.channels = [cross_section_mf33, decay_constants]",
                        ),
                    };
                    block["channels"] = json!(channels);
                    self.settle(&entries, status, detail);
                }
                None => self.settle(
                    &entries,
                    Status::Noted,
                    "no uncertainty block is emitted, so the choice of contributions has no effect",
                ),
            }
        }
        let overrides = std::mem::take(&mut self.unc.error_overrides);
        let entries = std::mem::take(&mut self.unc.error_entries);
        if !overrides.is_empty() {
            if uncertainty.is_some() && !channel_two_only {
                let listing: Vec<String> = overrides
                    .iter()
                    .map(|item| {
                        format!(
                            "{} MT {} = {}",
                            item.nuclide,
                            item.mt.unwrap_or_default(),
                            item.value
                        )
                    })
                    .collect();
                self.settle(
                    &entries,
                    Status::Approximated,
                    format!(
                        "overrides cross_section_uncertainty: {}. The fractional error is read as the relative standard uncertainty of that reaction's collapsed cross section, and ACTINV rescales the reaction's covariance to it with the correlations unchanged; FISPACT-II applies it inside a sensitivity run",
                        listing.join(", ")
                    ),
                );
                self.overrides.extend(overrides);
            } else {
                let reason = if channel_two_only {
                    "UNCTYPE 2 leaves the cross-section channel out"
                } else {
                    "no uncertainty block is emitted (it needs a UNCERTAINTY level, a covariance sidecar and none of the blockers), and an override of the cross-section uncertainty needs the cross-section channel"
                };
                let open: Vec<usize> = entries
                    .into_iter()
                    .filter(|index| {
                        self.entries
                            .get(*index)
                            .is_some_and(|entry| entry.status != Status::Unsupported)
                    })
                    .collect();
                self.settle(
                    &open,
                    Status::Unsupported,
                    format!("a per-reaction cross-section uncertainty cannot be applied: {reason}"),
                );
            }
        }
        Ok(())
    }

    /// Settle the fission keywords against the `--fission-yields` files. Returns the
    /// `fission_yields` section (`None` when fission-product yields stay off) and the FISCHOOSE list
    /// with its entries, which the pathways section settles.
    #[allow(clippy::type_complexity)]
    fn settle_fission(&mut self) -> (Option<Value>, Option<(Vec<String>, Vec<usize>)>) {
        let rec = std::mem::take(&mut self.fission);
        let files = self.ctx.fission_yields.clone();
        if let Some(entries) = &rec.nofiss {
            self.settle(
                entries,
                Status::Mapped,
                "fission_yields.files stays empty: no fission-product yields enter the rate matrix, as FISPACT-II skips the yield data (the default without USEFISSION; --fission-yields is not applied)",
            );
        }
        let mut refusal: Option<(Status, String)> = None;
        if rec.nofiss.is_some() {
            refusal = Some((
                Status::Noted,
                "NOFISS keeps the yield data out, so no fission products are produced".to_string(),
            ));
        } else if !self.projectile.is_neutron() {
            refusal = Some((
                Status::Unsupported,
                "ACTINV's fission-yield files are for neutron activation only".to_string(),
            ));
        } else if files.is_empty() {
            refusal = Some((
                Status::Unsupported,
                "fission-product yields are spec fission_yields.files, hash-pinned ENDF neutron-induced yield files (one per parent) that the deck does not name: pass --fission-yields FILE..."
                    .to_string(),
            ));
        }
        let mut section = None;
        let mut parents_detail = String::new();
        if let Some(entries) = &rec.use_fission {
            match &refusal {
                Some((status, detail)) => self.settle(entries, *status, detail.clone()),
                None => {
                    let supplied: Vec<(i32, i32)> = files
                        .iter()
                        .map(|file| (file.parent_za, file.parent_liso))
                        .collect();
                    let (parents, detail) = fission_parent_selection(
                        &supplied,
                        rec.yield_parents.as_ref().map(|(list, _)| list),
                    );
                    parents_detail = detail;
                    let mut map = Map::new();
                    map.insert(
                        "files".into(),
                        json!(files
                            .iter()
                            .map(|file| json!({"path": file.file.path, "sha256": file.file.sha256}))
                            .collect::<Vec<_>>()),
                    );
                    if rec.cumulative.is_some() {
                        map.insert("yields".into(), json!("cumulative"));
                    }
                    if let Some(parents) = parents {
                        map.insert("parents".into(), parents);
                    }
                    section = Some(Value::Object(map));
                    self.settle(
                        entries,
                        Status::Mapped,
                        format!(
                            "fission_yields.files = the {} --fission-yields file(s) (parents {}); energy stays at the spec default (spectrum_average); {parents_detail}",
                            files.len(),
                            files
                                .iter()
                                .map(|file| nuclide_label(file.parent_za, file.parent_liso))
                                .collect::<Vec<_>>()
                                .join(", ")
                        ),
                    );
                }
            }
        }
        if let Some(entries) = &rec.cumulative {
            if section.is_some() {
                self.settle(
                    entries,
                    Status::Mapped,
                    "fission_yields.yields = \"cumulative\" (the MT=459 yields instead of the MT=454 independent ones)",
                );
            } else {
                self.settle(
                    entries,
                    Status::Noted,
                    "no fission-product yields are in the spec, so the yield set has no effect",
                );
            }
        }
        if let Some((_, entries)) = &rec.yield_parents {
            if section.is_some() {
                self.settle(
                    entries,
                    Status::Mapped,
                    format!("fission_yields.parents: {parents_detail}"),
                );
            } else {
                self.settle(
                    entries,
                    Status::Noted,
                    "no fission-product yields are in the spec (USEFISSION and yield files are both needed), so the parent selection has no effect",
                );
            }
        }
        (section, rec.choose)
    }

    /// Settle the self-shielding keywords against `--resonance` and `--self-shielding-table`.
    /// Returns the `self_shielding` section and whether it needs a density the deck lacks.
    fn settle_self_shielding(&mut self, groups: usize) -> (Option<Value>, bool) {
        let rec = std::mem::take(&mut self.shield);
        let table = self.ctx.self_shielding_table.clone();
        let resonance = self.ctx.resonance.clone();
        let geometry_asked = rec.geometry.is_some();
        let table_asked = rec.probtable.is_some() || rec.dilution.is_some();
        let companions: Vec<usize> = [
            rec.choose.as_ref().map(|(_, entries)| entries),
            rec.mass.as_ref(),
            rec.fuel.as_ref(),
        ]
        .into_iter()
        .flatten()
        .flatten()
        .copied()
        .collect();
        let requests: Vec<usize> = rec
            .geometry
            .iter()
            .flat_map(|(_, entries)| entries.iter().copied())
            .chain(rec.probtable.iter().flatten().copied())
            .chain(
                rec.dilution
                    .iter()
                    .flat_map(|(_, entries)| entries.iter().copied()),
            )
            .collect();
        if !geometry_asked && !table_asked {
            // names or a composition with nothing that uses them
            let (status, detail) = if table.is_some() || !resonance.is_empty() {
                (
                    Status::Noted,
                    "no SSFGEOMETRY, PROBTABLE or SSFDILUTION asks for self-shielding, so this selection has no effect",
                )
            } else {
                (
                    Status::Unsupported,
                    "self-shielding needs spec self_shielding, a hash-pinned ENDF resonance evaluation per nuclide (--resonance FILE...) for the geometry model or a Bondarenko table (--self-shielding-table FILE), which the deck does not name",
                )
            };
            self.settle(&companions, status, detail);
            return (None, false);
        }
        let refusal: Option<String> = if !self.projectile.is_neutron() {
            Some("self-shielding is neutron-only in ACTINV".to_string())
        } else if geometry_asked && table_asked {
            Some("FISPACT-II does not allow SSFGEOMETRY beside PROBTABLE, and ACTINV's self-shielding takes a table or a geometry, not both".to_string())
        } else if geometry_asked && resonance.is_empty() {
            Some("geometry self-shielding is spec self_shielding.geometry, which needs hash-pinned ENDF resonance evaluations the deck does not name: pass --resonance FILE...".to_string())
        } else if table_asked && table.is_none() {
            Some("table self-shielding is spec self_shielding.table, a hash-pinned Bondarenko table the deck does not name: pass --self-shielding-table FILE".to_string())
        } else {
            None
        };
        if let Some(reason) = refusal {
            self.settle(&requests, Status::Unsupported, reason.clone());
            self.settle(&companions, Status::Unsupported, reason);
            return (None, false);
        }
        let composition = "ACTINV self-shields the problem's own material (MASS or FUEL, with the density), not a separate self-shielding composition; the two agree only when the deck gives the same material to both";
        if let Some((geometry, entries)) = &rec.geometry {
            let mut map = Map::new();
            map.insert("shape".into(), json!(geometry.shape));
            map.insert("length_cm".into(), json!(geometry.length_cm));
            if let Some(height) = geometry.height_cm {
                map.insert("height_cm".into(), json!(height));
            }
            if let Some(density) = self.density {
                map.insert("density_g_cm3".into(), json!(density));
            }
            map.insert(
                "resonance_files".into(),
                json!(resonance
                    .iter()
                    .map(|file| json!({"path": file.path, "sha256": file.sha256}))
                    .collect::<Vec<_>>()),
            );
            let chosen: Vec<String> = rec
                .choose
                .as_ref()
                .map(|(names, _)| names.clone())
                .unwrap_or_default();
            if !chosen.is_empty() {
                map.insert("choose".into(), json!(chosen));
            }
            let density = match self.density {
                Some(density) => format!("density_g_cm3 = {density} (DENSITY)"),
                None => "the deck has no DENSITY, so add self_shielding.geometry.density_g_cm3 or material.density_g_cm3 (see required_additions)".to_string(),
            };
            let size = match geometry.height_cm {
                Some(height) => format!(
                    "length_cm = {} (radius), height_cm = {height}",
                    geometry.length_cm
                ),
                None => format!("length_cm = {}", geometry.length_cm),
            };
            self.settle(
                entries,
                Status::Mapped,
                format!(
                    "self_shielding.geometry: shape \"{}\", {size}, {density}, {} resonance file(s) from --resonance",
                    geometry.shape,
                    resonance.len()
                ),
            );
            if let Some((names, entries)) = &rec.choose {
                if names.is_empty() {
                    self.settle(
                        entries,
                        Status::Noted,
                        "names no nuclide, so no restriction applies",
                    );
                } else {
                    self.settle(
                        entries,
                        Status::Mapped,
                        format!("self_shielding.geometry.choose = {}", json!(names)),
                    );
                }
            }
            for entries in [&rec.mass, &rec.fuel].into_iter().flatten() {
                self.settle(entries, Status::Approximated, composition);
            }
            return (
                Some(json!({"geometry": Value::Object(map)})),
                self.density.is_none(),
            );
        }
        // table mode
        let table = table.expect("table mode is refused without --self-shielding-table");
        let mut map = Map::new();
        map.insert(
            "table".into(),
            json!({"path": table.path, "sha256": table.sha256}),
        );
        if let Some(entries) = &rec.probtable {
            self.settle(
                entries,
                Status::Approximated,
                "self_shielding.table = --self-shielding-table: Bondarenko self-shielding factors on a dilution x temperature grid are applied in place of FISPACT-II's probability tables (a multiplicative factor on the collapsed cross section, whichever way the deck sets PROBTABLE's switches)",
            );
        }
        if let Some((names, dilution_entries)) = &rec.dilution {
            let mut converted: BTreeMap<String, BTreeMap<usize, f64>> = BTreeMap::new();
            let mut bad: Option<String> = None;
            for (name, rows) in names {
                for (group, value) in rows {
                    // FISPACT-II numbers the groups from the highest energy; ACTINV's index is 0-based
                    // and ascending in energy
                    match usize::try_from(*group)
                        .ok()
                        .filter(|group| *group <= groups)
                    {
                        Some(group) => {
                            converted
                                .entry(name.clone())
                                .or_default()
                                .insert(groups - group, *value);
                        }
                        None => {
                            bad.get_or_insert_with(|| {
                                format!(
                                    "{name} group {group} is outside the library's {groups} groups"
                                )
                            });
                        }
                    }
                }
            }
            match bad {
                Some(reason) => {
                    self.settle(dilution_entries, Status::Unsupported, reason);
                }
                None => {
                    map.insert("dilution_overrides".into(), json!(converted));
                    let pick = if rec.probtable.is_none() {
                        "; SSFDILUTION alone selects the table mode, since there is no PROBTABLE"
                    } else {
                        ""
                    };
                    self.settle(
                        dilution_entries,
                        Status::Mapped,
                        format!(
                            "self_shielding.dilution_overrides: FISPACT-II group g (1 = highest energy) becomes ACTINV index {groups} - g (0-based, ascending energy) of the {groups}-group library, so group 1 is index {} and group {groups} is index 0{pick}",
                            groups - 1
                        ),
                    );
                }
            }
        }
        if let Some((_, entries)) = &rec.choose {
            self.settle(
                entries,
                Status::Approximated,
                "ACTINV shields every material nuclide the table covers; the names do not restrict it",
            );
        }
        for entries in [&rec.mass, &rec.fuel].into_iter().flatten() {
            self.settle(entries, Status::Approximated, composition);
        }
        (Some(Value::Object(map)), false)
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
            let detail = lost_time_detail(&pending, None);
            match pending.entry {
                Some(index) => self.set_entry(Some(index), Status::Unsupported, detail),
                None => self.entries.push(Entry {
                    keyword: "TIME".into(),
                    line: pending.line,
                    phase: Phase::Inventory,
                    status: Status::Unsupported,
                    detail,
                    args: String::new(),
                }),
            }
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
                    return Err(format!(
                        "{IRRADIATES_WITHOUT_FLUXES}: pass --fluxes FILE, or --files FILES with a `fluxes` entry"
                    ));
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
        } else if self.overrides.iter().any(|item| {
            item.kind.starts_with("cross_section") && item.kind != "cross_section_uncertainty"
        }) {
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
                let covariance = self.covariance_source(defaulted_library)?;
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

        let blocker = uncertainty_blocker;
        self.settle_uncertainty_keywords(&mut uncertainty, blocker, defaulted_library)?;
        let (fission_section, fission_choose) = self.settle_fission();
        let (shield_section, shield_needs_density) = self.settle_self_shielding(groups);

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
        let fission_used = fission_section.is_some();
        if let Some(section) = fission_section {
            spec.insert("fission_yields".into(), section);
        }
        let shielding_kind = shield_section.as_ref().map(|section| {
            if section.get("geometry").is_some() {
                "geometry"
            } else {
                "table"
            }
        });
        if let Some(section) = shield_section {
            spec.insert("self_shielding".into(), section);
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
            let sha256 = crate::radiological::bundled_sha256(name)?;
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
                    if let Some((names, _)) = &fission_choose {
                        let mut parents: Vec<&String> = Vec::new();
                        for name in names {
                            if !parents.contains(&name) {
                                parents.push(name);
                            }
                        }
                        if !parents.is_empty() {
                            pathways.insert("fission_parents".into(), json!(parents));
                        }
                    }
                    spec.insert("pathways".into(), Value::Object(pathways));
                }
            }
        }
        if let Some((names, entries)) = &fission_choose {
            let (status, detail) = if names.is_empty() {
                (
                    Status::Noted,
                    "names no actinide, so pathways keep every fission parent".to_string(),
                )
            } else if spec.contains_key("pathways") {
                (
                    Status::Mapped,
                    format!(
                        "pathways.fission_parents = {}: routes through a fission edge may start only from these parents; the inventory is unchanged, as in FISPACT-II",
                        json!(names)
                    ),
                )
            } else {
                (
                    Status::Noted,
                    "restricts the parents of fission pathways, and the deck requests no pathways (PATH or ROUTES), so there is nothing to restrict".to_string(),
                )
            };
            self.settle(entries, status, detail);
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
        if shield_needs_density {
            // geometry self-shielding turns atoms per gram into number densities with a density
            // that no DENSITY keyword gave
            if validated["material"].get("density_g_cm3").is_none() {
                validated["material"]["density_g_cm3"] = json!(1.0);
            }
            required.push(json!({
                "field": "self_shielding.geometry.density_g_cm3",
                "keyword": "SSFGEOMETRY",
                "line": self.entries.iter().find(|entry| entry.keyword == "SSFGEOMETRY").map(|entry| entry.line),
                "reason": "geometry self-shielding needs the target density; the deck has no DENSITY, so add self_shielding.geometry.density_g_cm3 (or material.density_g_cm3); until it is added the spec does not validate",
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
        // options that were supplied and that nothing in the deck used
        let mut unused: Vec<&str> = Vec::new();
        if !self.ctx.fission_yields.is_empty() && !fission_used {
            unused.push("--fission-yields");
        }
        if !self.ctx.resonance.is_empty() && shielding_kind != Some("geometry") {
            unused.push("--resonance");
        }
        if self.ctx.self_shielding_table.is_some() && shielding_kind != Some("table") {
            unused.push("--self-shielding-table");
        }
        let mut report = json!({
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
        if !unused.is_empty() {
            report["options_unused"] = json!(unused);
        }
        Ok(Conversion {
            spec: Value::Object(spec),
            report,
            counts,
            unsupported,
        })
    }
}

/// Command executions the PULSE loops expand to (a zero-count body is interpreted once), refused
/// above [`MAX_EXPANDED_COMMANDS`] before anything is expanded.
fn expanded_work(nodes: &[Node]) -> Result<u64, String> {
    let mut total = 0u64;
    for node in nodes {
        match node {
            Node::Cmd(_) => total = total.saturating_add(1),
            Node::Pulse { cmd, count, body } => {
                let inner = expanded_work(body)?;
                let work = inner.saturating_add(1).saturating_mul((*count).max(1));
                total = total.saturating_add(work);
                if work > MAX_EXPANDED_COMMANDS || total > MAX_EXPANDED_COMMANDS {
                    return Err(format!(
                        "line {}: the PULSE loops expand to more than {MAX_EXPANDED_COMMANDS} command executions; reduce the repeat counts",
                        cmd.line
                    ));
                }
            }
        }
    }
    Ok(total)
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
    expanded_work(&nodes)?;
    let mut interp = Interp::new(ctx);
    interp.run_nodes(&nodes, true)?;
    let ended = parser.ended;
    interp.finish(ended)
}

// ---------------------------------------------------------------------------------------------
// Files and flux files
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

/// A fluxes file's text as a [`LoadedFlux`]. `path` only labels the spectrum in the report and in
/// errors, so the browser can pass the picked file's name.
pub fn load_flux_text(path: &str, text: &str) -> Result<LoadedFlux, String> {
    let groups = infer_group_count(text).map_err(|error| format!("{path}: {error}"))?;
    let (values, wall_loading, title) = parse_fispact_fluxes_text(text, groups)?;
    Ok(LoadedFlux {
        path: path.to_string(),
        values,
        wall_loading_mw_m2: wall_loading,
        title,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::parse_duration;

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
            ..ImportContext::default()
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
    fn flux_text_loads_without_a_file() {
        let fluxes = " 1.0 2.0 3.0\n 0.5\nPosition #2\n";
        let loaded = load_flux_text("picked.fluxes", fluxes).unwrap();
        assert_eq!(loaded.path, "picked.fluxes");
        assert_eq!(loaded.values, vec![1.0, 2.0, 3.0]);
        assert_eq!(loaded.wall_loading_mw_m2, 0.5);
        assert_eq!(loaded.title, "Position #2");
        let error = load_flux_text("picked.fluxes", "1.0 x\ntitle\n").unwrap_err();
        assert!(
            error.starts_with("picked.fluxes: non-numeric word 'x'"),
            "{error}"
        );
        // the loaded spectrum reaches the conversion with no file access
        let ctx = ImportContext {
            flux: Some(loaded),
            library: Some("library.npz".into()),
            decay: Some("decay.endf".into()),
            covariance: None,
            ..ImportContext::default()
        };
        let error = convert(&deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS"), &ctx)
            .err()
            .unwrap();
        assert!(error.starts_with("picked.fluxes has 3 groups"), "{error}");
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
        assert!(entry_detail(&bare, "POWER").contains("301 444"));
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
        // DOSE 1 is the default contact dose and, before any step, undoes DOSE 2 (whose entry
        // then says it was superseded)
        let contact = convert(
            &deck_with("DOSE 2 0.5\nDOSE 1\nFLUX 1E10\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        assert!(contact.spec.get("photon").is_none());
        assert_eq!(entry_status(&contact, "DOSE"), vec!["noted", "noted"]);
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
            crate::radiological::bundled_sha256("us-49cfr173-a2")
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

    // -----------------------------------------------------------------------------------------
    // P140 phase 1: regression tests for the hardening review. Each asserts the correct
    // behaviour and fails on the importer as it stood when they were written.
    // -----------------------------------------------------------------------------------------

    /// The shipped conderc-fission deck `U235pulse.i`, verbatim (`NOHEAD` and `TOL`).
    const U235_PULSE: &str = "CLOBBER
NOHEAD
GETXS 0
GETDECAY 0
FISPACT
*Uranium 235 fission pulse
USEFISSION
FISYIELD 1 U235
FUEL 1
  U235 1.0E+24
MIND 1.0
DENSITY 19.0
HAZA
HALF
OVER U235
  ACROSS U236 0.0
GRAPH 5 2 1 1 2 3 4 5
UNCERT 2
SORTDOMINANT 100 100
TOL 0 1E-2 1E-6
FISCHOOSE 1 U235
ATOMS
FLUX 1.0E+22
TIME 1.0E-06
ATOMS
FLUX 0.
ZERO
";

    fn entries_on_line(conversion: &Conversion, line: usize) -> Vec<&Value> {
        entries(conversion)
            .iter()
            .filter(|entry| entry["line"] == line)
            .collect()
    }

    /// The entry text the importer uses for a word it did not recognise.
    fn reads_as_unrecognised(entry: &Value) -> bool {
        let detail = entry["detail"].as_str().unwrap_or_default().to_lowercase();
        detail.contains("cannot tell")
            || detail.contains("not a fispact-ii keyword")
            || detail.contains("unknown to this importer")
    }

    fn mentions_any(text: &str, needles: &[&str]) -> bool {
        let text = text.to_lowercase();
        needles
            .iter()
            .any(|needle| text.contains(&needle.to_lowercase()))
    }

    /// Convert a deck whose only keyword of interest sits on line 5, followed by one irradiation.
    fn convert_line_five(keyword_line: &str) -> Conversion {
        let deck = deck_with(&format!("{keyword_line}\nFLUX 1E10\nTIME 1 HOURS ATOMS"));
        convert(&deck, &context()).unwrap()
    }

    /// Convert on a worker thread with a large stack; `None` when it did not finish in time. A
    /// runaway conversion keeps spinning on its detached thread until the test process exits, which
    /// is why only decks the correct importer refuses at once may be passed here.
    fn convert_bounded(deck: String, seconds: u64) -> Option<Result<Conversion, String>> {
        let (sender, receiver) = std::sync::mpsc::channel();
        std::thread::Builder::new()
            .stack_size(64 << 20)
            .spawn(move || {
                let _ = sender.send(convert(&deck, &context()));
            })
            .unwrap();
        receiver
            .recv_timeout(std::time::Duration::from_secs(seconds))
            .ok()
    }

    // ---- F1: abbreviations and keywords missing from the table ----

    #[test]
    fn p140_if_f1_three_letter_abbreviations_resolve() {
        assert_eq!(
            resolve_keyword("TOL").map(|keyword| keyword.0),
            Some("TOLERANCE")
        );
        assert_eq!(
            resolve_keyword("tol").map(|keyword| keyword.0),
            Some("TOLERANCE")
        );
        assert_eq!(
            resolve_keyword("HAZ").map(|keyword| keyword.0),
            Some("HAZARDS")
        );
    }

    #[test]
    fn p140_if_f1_shipped_fission_deck_tol_and_nohead_are_noted() {
        let conversion = convert(U235_PULSE, &context()).unwrap();
        // NOHEAD is on line 2 of the shipped deck
        let nohead = entries_on_line(&conversion, 2);
        assert_eq!(nohead.len(), 1, "{nohead:?}");
        assert_eq!(nohead[0]["status"], "noted", "{nohead:?}");
        assert!(!reads_as_unrecognised(nohead[0]), "{nohead:?}");
        // TOL carries the arguments `0 1E-2 1E-6`
        let tol: Vec<&Value> = entries(&conversion)
            .iter()
            .filter(|entry| entry["args"] == "0 1E-2 1E-6")
            .collect();
        assert_eq!(tol.len(), 1, "{tol:?}");
        assert_eq!(tol[0]["status"], "noted", "{tol:?}");
        assert!(!reads_as_unrecognised(tol[0]), "{tol:?}");
        // nothing in the deck is reported as a misspelled keyword
        assert!(
            !entries(&conversion).iter().any(reads_as_unrecognised),
            "{:?}",
            entries(&conversion)
        );
    }

    #[test]
    fn p140_if_f1_noheader_and_mcseed_are_recognised() {
        for keyword_line in ["NOHEAD", "NOHEADER", "TOL 0 1E-2 1E-6", "TOLER 0 1E-2 1E-6"] {
            let conversion = convert_line_five(keyword_line);
            let on_line = entries_on_line(&conversion, 5);
            assert_eq!(on_line.len(), 1, "{keyword_line}: {on_line:?}");
            assert_eq!(on_line[0]["status"], "noted", "{keyword_line}: {on_line:?}");
            assert_eq!(conversion.counts.unsupported, 0, "{keyword_line}");
        }
        // MCSEED seeds MCSAMPLE: whatever its status, it is a known keyword
        let conversion = convert_line_five("MCSEED 12345");
        let on_line = entries_on_line(&conversion, 5);
        assert!(!on_line.is_empty());
        assert!(!on_line.iter().any(|entry| reads_as_unrecognised(entry)));
    }

    #[test]
    fn p140_if_f1_deprecated_keywords_are_recognised() {
        // COLLAPSE = GETXS 1, ARRAY = GETDECAY 0, AINPUT = GETXS 0 GETDECAY 0: library selection
        // that is mapped, approximated or noted, never "not a keyword" and never unsupported
        for keyword in ["COLLAPSE", "ARRAY", "AINPUT"] {
            let conversion = convert_line_five(keyword);
            let on_line = entries_on_line(&conversion, 5);
            assert!(!on_line.is_empty(), "{keyword} has no entry");
            for entry in &on_line {
                assert!(!reads_as_unrecognised(entry), "{keyword}: {entry}");
                assert_ne!(entry["status"], "unsupported", "{keyword}: {entry}");
            }
        }
        // the print and no-op switches of the same FISPACT-II inventory
        for keyword in [
            "CONV",
            "DOMINANT",
            "EAFVERSION",
            "ENFA",
            "LEVEL",
            "LINA",
            "LOOPS",
            "NEWFILE",
            "SEQNUMBER",
            "SEQUENTIAL",
            "TAPA",
        ] {
            let conversion = convert_line_five(keyword);
            let on_line = entries_on_line(&conversion, 5);
            assert!(!on_line.is_empty(), "{keyword} has no entry");
            assert!(
                !on_line.iter().any(|entry| reads_as_unrecognised(entry)),
                "{keyword}: {on_line:?}"
            );
        }
    }

    // ---- F2: an unknown keyword after a Words-arity keyword is not an argument ----

    #[test]
    fn p140_if_f2_unknown_keyword_after_a_words_keyword_gets_its_own_entry() {
        for head in [
            "PATH Co58",
            "ROUTES Co58",
            "OVER Co58 ALAM 5 DAYS",
            "PARTITION 1 Fe 0.5",
            "POWER 1.0 1 301",
            "BREMSSTRAHLUNG 1 Co60",
        ] {
            let conversion = convert_line_five(&format!("{head}\nFROBNICATE"));
            assert_eq!(
                entry_status(&conversion, "FROBNICATE"),
                vec!["unsupported"],
                "after '{head}': {:?}",
                entries(&conversion)
            );
            assert!(
                conversion
                    .unsupported
                    .iter()
                    .any(|(keyword, line)| keyword == "FROBNICATE" && *line == 6),
                "after '{head}': {:?}",
                conversion.unsupported
            );
        }
    }

    #[test]
    fn p140_if_f2_a_valid_partition_stays_mapped_beside_an_unknown_keyword() {
        let conversion = convert_line_five("PARTITION 1 Fe 0.5\nFROBNICATE");
        assert_eq!(entry_status(&conversion, "PARTITION"), vec!["mapped"]);
        assert_eq!(entry_status(&conversion, "FROBNICATE"), vec!["unsupported"]);
        assert_eq!(
            conversion.spec["schedule"][0]["partition"],
            json!({"Fe": 0.5})
        );
        // the keyword is not folded into PATH's prose either
        let path = convert_line_five("PATH Co58\nFROBNICATE");
        assert!(!entry_detail(&path, "PATH").contains("FROBNICATE"));
    }

    // ---- F3: a superseded or trailing TIME is unsupported, deferral semantics kept ----

    #[test]
    fn p140_if_f3_a_superseded_time_is_unsupported_with_its_duration_and_flux() {
        let deck = deck_with("FLUX 1E14\nTIME 1 HOURS\nFLUX 0\nTIME 1 DAYS ATOMS");
        let conversion = convert(&deck, &context()).unwrap();
        // the documented deferral is kept: one step, the later interval
        let schedule = conversion.spec["schedule"].as_array().unwrap();
        assert_eq!(schedule.len(), 1);
        assert_eq!(seconds(&schedule[0]), 86_400.0);
        // the interval that no output keyword ran is strict-visible and says what was lost
        let lost: Vec<&Value> = entries(&conversion)
            .iter()
            .filter(|entry| entry["keyword"] == "TIME" && entry["status"] == "unsupported")
            .collect();
        assert!(
            !lost.is_empty(),
            "no unsupported TIME entry: {:?}",
            entries(&conversion)
        );
        assert!(conversion.counts.unsupported >= 1);
        let detail = lost
            .iter()
            .map(|entry| entry["detail"].as_str().unwrap())
            .collect::<Vec<_>>()
            .join(" ");
        assert!(mentions_any(&detail, &["3600", "1 h"]), "{detail}");
        assert!(
            mentions_any(&detail, &["1e14", "1e+14", "100000000000000"]),
            "{detail}"
        );
    }

    #[test]
    fn p140_if_f3_a_trailing_time_is_unsupported_with_its_duration_and_flux() {
        let deck = deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS\nTIME 3 HOURS");
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(conversion.spec["schedule"].as_array().unwrap().len(), 1);
        let lost: Vec<&Value> = entries(&conversion)
            .iter()
            .filter(|entry| entry["keyword"] == "TIME" && entry["status"] == "unsupported")
            .collect();
        assert_eq!(lost.len(), 1, "{:?}", entries(&conversion));
        assert!(conversion.counts.unsupported >= 1);
        let detail = lost[0]["detail"].as_str().unwrap();
        assert!(mentions_any(detail, &["10800", "3 h"]), "{detail}");
        assert!(
            mentions_any(detail, &["1e10", "1e+10", "10000000000"]),
            "{detail}"
        );
    }

    // ---- F4: OVER after the first TIME is not mapped from t = 0 ----

    #[test]
    fn p140_if_f4_over_after_the_first_step_is_not_reported_as_mapped() {
        let deck = deck_with(
            "FLUX 1E10\nTIME 1 YEARS ATOMS\nOVER Co58\n ALAM 100 DAYS\nFLUX 0\nTIME 1 DAYS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        for keyword in ["ALAM", "OVER"] {
            let status = entry_status(&conversion, keyword);
            assert_eq!(status.len(), 1, "{keyword}");
            assert!(
                status[0] == "approximated" || status[0] == "unsupported",
                "{keyword} after a step is '{}', not a from-t=0 mapping: {:?}",
                status[0],
                entries(&conversion)
            );
        }
        // an OVER before the first TIME is still a plain mapping
        let early = deck_with("OVER Co58\n ALAM 100 DAYS\nFLUX 1E10\nTIME 1 YEARS ATOMS");
        let early = convert(&early, &context()).unwrap();
        assert_eq!(entry_status(&early, "ALAM"), vec!["mapped"]);
    }

    // ---- F5: bounded work for nested and deep PULSE ----

    #[test]
    fn p140_if_f5_nested_huge_pulse_counts_are_refused_not_run() {
        let deck = deck_with(
            "PULSE 1000000000\n PULSE 1000000000\n  FLUX 1E10\n ENDPULSE\nENDPULSE\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let result = convert_bounded(deck, 10).expect(
            "no answer within 10 s: 1e9 x 1e9 repetitions of a step-free PULSE body were run instead of refused",
        );
        let error = result.err().expect("1e18 loop iterations were accepted");
        assert!(error.to_lowercase().contains("pulse"), "{error}");
    }

    #[test]
    fn p140_if_f5_deeply_nested_pulses_are_refused() {
        // 4000 levels recurse fine on the 64 MiB worker stack of the current code, so a failure
        // here is the missing depth limit, not a crash; ~10^5 levels overflow the default stack
        // (see target/p140/repro/if/deep_pulse.i)
        let depth = 4000;
        let mut deck = String::from("FISPACT\n* t\nMASS 1E-3 1\n FE 100\n");
        deck.push_str(&"PULSE 1\n".repeat(depth));
        deck.push_str("FLUX 1E10\nTIME 1 SECS ATOMS\n");
        deck.push_str(&"ENDPULSE\n".repeat(depth));
        deck.push_str("END\n");
        let result = convert_bounded(deck, 30).expect("no answer within 30 s");
        let error = result
            .err()
            .expect("a 4000-deep PULSE nest was accepted: there is no nesting limit");
        assert!(error.to_lowercase().contains("pulse"), "{error}");
    }

    // ---- F6: keywords inside PULSE 0 are still reported ----

    #[test]
    fn p140_if_f6_keywords_inside_a_zero_count_pulse_are_reported() {
        let deck = deck_with(
            "PULSE 0\n SSFGEOMETRY 1 0.5\n FROBNICATE\n FLUX 7E7\nENDPULSE\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        // SSFGEOMETRY is deferred to the deck's end, which the skipped body never reaches
        assert_eq!(entry_status(&conversion, "SSFGEOMETRY"), vec!["noted"]);
        assert_eq!(entry_status(&conversion, "FROBNICATE"), vec!["unsupported"]);
        assert!(conversion.counts.unsupported >= 1);
        // the skipped body has no effect on the problem: its FLUX is not the spectrum total
        assert_eq!(conversion.spec["spectrum"]["total"], 1.0e10);
        assert_eq!(conversion.spec["schedule"].as_array().unwrap().len(), 1);
    }

    // ---- F7: repeated deferred keywords and loop bodies ----

    #[test]
    fn p140_if_f7_a_repeated_uncertainty_leaves_no_unsettled_entry() {
        let deck = deck_with("UNCERT 2\nUNCERT 0\nFLUX 1E10\nTIME 1 HOURS ATOMS");
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(entry_status(&conversion, "UNCERTAINTY").len(), 2);
        for entry in entries(&conversion)
            .iter()
            .filter(|entry| entry["keyword"] == "UNCERTAINTY")
        {
            assert!(
                !entry["detail"].as_str().unwrap().contains(DEFERRED_DETAIL),
                "stale placeholder: {entry}"
            );
        }
        // the first request is said to be superseded, not left claiming a status of its own
        let first = entries_on_line(&conversion, 5)[0];
        assert!(
            mentions_any(
                first["detail"].as_str().unwrap(),
                &["supersed", "replaced", "overridden", "later", "last"]
            ),
            "{first}"
        );
        // the last request is the one that applies
        let deck = deck_with("UNCERT 0\nUNCERT 2\nFLUX 1E10\nTIME 1 HOURS ATOMS");
        let conversion = convert(&deck, &context_with_covariance()).unwrap();
        assert!(conversion.spec.get("uncertainty").is_some());
        let statuses = entry_status(&conversion, "UNCERTAINTY");
        assert_eq!(statuses.last().unwrap(), "mapped");
        assert!(!entries(&conversion)
            .iter()
            .any(|entry| entry["detail"].as_str().unwrap().contains(DEFERRED_DETAIL)));
    }

    #[test]
    fn p140_if_f7_a_repeated_spectrum_conversion_leaves_no_unsettled_entry() {
        let deck = deck_with(
            "GRPCONVERT 709 1\nGRPCONVERT 709 2\nCNVTYPE 0\nCNVTYPE 1\nFLUX 1E10\nTIME 1 HOURS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(entry_status(&conversion, "GRPCONVERT").len(), 2);
        assert_eq!(entry_status(&conversion, "CNVTYPE").len(), 2);
        for entry in entries(&conversion) {
            assert!(
                !entry["detail"].as_str().unwrap().contains(DEFERRED_DETAIL),
                "stale placeholder: {entry}"
            );
        }
    }

    #[test]
    fn p140_if_f7_uncertainty_inside_a_pulse_body_is_settled() {
        let deck = deck_with("PULSE 2\n UNCERT 2\n FLUX 1E10\n TIME 1 SECS ATOMS\nENDPULSE");
        let conversion = convert(&deck, &context_with_covariance()).unwrap();
        assert_eq!(conversion.spec["schedule"].as_array().unwrap().len(), 2);
        assert!(conversion.spec.get("uncertainty").is_some());
        // the second repetition (not recorded) must not erase the entry to settle
        assert_eq!(entry_status(&conversion, "UNCERTAINTY"), vec!["mapped"]);
    }

    // ---- F8: DOSE 1 after DOSE 2 ----

    #[test]
    fn p140_if_f8_the_spec_and_the_dose_entries_agree() {
        let deck = deck_with(
            "DOSE 2 1.0\nFLUX 1E10\nTIME 1 HOURS ATOMS\nDOSE 1\nFLUX 0\nTIME 1 DAYS ATOMS",
        );
        let conversion = convert(&deck, &context()).unwrap();
        let has_point_source = conversion
            .spec
            .get("photon")
            .and_then(|photon| photon.get("point_source"))
            .is_some();
        let statuses = entry_status(&conversion, "DOSE");
        assert_eq!(statuses.len(), 2);
        // a mapped DOSE 2 must be in the spec; if DOSE 1 erased it, the entry must say so
        assert!(
            (has_point_source && statuses[0] == "mapped") || statuses[0] != "mapped",
            "spec has no photon.point_source yet DOSE 2 is '{}' and DOSE 1 is '{}': {:?}",
            statuses[0],
            statuses[1],
            entries(&conversion)
        );
    }

    // ---- F9: encodings and line ends ----

    #[test]
    fn p140_if_f9_a_utf8_bom_is_not_part_of_the_first_keyword() {
        let deck = format!("\u{feff}{}", deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS"));
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(
            conversion.counts.unsupported, 0,
            "{:?}",
            conversion.unsupported
        );
        assert_eq!(conversion.spec["title"], "t");
        // and before a `*` title line
        let lexed = lex("\u{feff}* a title\nFLUX 1");
        assert!(lexed.tokens[0].star, "{:?}", lexed.tokens);
        assert_eq!(lexed.tokens[0].text, "a title");
    }

    #[test]
    fn p140_if_f9_bare_cr_line_ends_split_lines() {
        let deck = deck_with("FLUX 1E10\nTIME 1 HOURS ATOMS").replace('\n', "\r");
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(conversion.spec["title"], "t");
        assert_eq!(
            conversion.counts.unsupported, 0,
            "{:?}",
            conversion.unsupported
        );
        assert_eq!(conversion.spec["schedule"].as_array().unwrap().len(), 1);
        let lexed = lex("FLUX 1\rTIME 5\rATOMS\r");
        let lines: Vec<usize> = lexed.tokens.iter().map(|token| token.line).collect();
        assert_eq!(lines, vec![1, 1, 2, 2, 3]);
    }

    // ---- F14: POWER detail and arguments ----

    #[test]
    fn p140_if_f14_power_without_an_mt_list_does_not_claim_an_empty_list() {
        let conversion = convert(
            &deck_with("DENSITY 7.874\nPOWER 150.0\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        let detail = entry_detail(&conversion, "POWER");
        assert!(!detail.contains("MT list ''"), "{detail}");
    }

    #[test]
    fn p140_if_f14_power_reads_its_declared_count_of_mt_numbers() {
        let conversion = convert(
            &deck_with("DENSITY 7.874\nPOWER 150.0 2 301 444\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        let detail = entry_detail(&conversion, "POWER");
        // the count `2` is not an MT number
        assert!(!detail.contains("'2 301 444'"), "{detail}");
        assert!(mentions_any(&detail, &["301 444", "301, 444"]), "{detail}");
        // a list that is exactly MT 301 stays silent about the MT list
        let plain = convert(
            &deck_with("DENSITY 7.874\nPOWER 150.0 1 301\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        assert!(!entry_detail(&plain, "POWER").contains("MT list"));
    }

    #[test]
    fn p140_if_f14_power_flags_words_after_its_mt_list() {
        let conversion = convert(
            &deck_with("DENSITY 7.874\nPOWER 150.0 1 301 999\nTIME 1 HOURS ATOMS"),
            &context(),
        )
        .unwrap();
        let detail = entry_detail(&conversion, "POWER");
        assert!(
            mentions_any(
                &detail,
                &[
                    "ignored",
                    "not interpreted",
                    "extra",
                    "unread",
                    "trailing",
                    "beyond"
                ]
            ),
            "{detail}"
        );
    }

    // ---- H3: sub-keyword entries must not copy the parent's argument list ----

    #[test]
    fn p140_if_h3_over_many_subkeywords_is_linear() {
        // 20000 sub-keyword lines under one OVER: each used to clone the 20000-word argument list
        let mut body = String::from("OVER FE56\n");
        for _ in 0..10_000 {
            body.push_str(" ACROSS FE57 0.5\n ALAM 10 DAYS\n");
        }
        body.push_str("FLUX 1E10\nTIME 1 HOURS ATOMS");
        let result = convert_bounded(deck_with(&body), 10)
            .expect("no answer within 10 s: OVER sub-keyword handling is not linear");
        // a clean refusal would also do; success must report every sub-keyword
        if let Ok(conversion) = result {
            assert_eq!(entry_status(&conversion, "ACROSS").len(), 10_000);
            assert_eq!(entry_status(&conversion, "ALAM").len(), 10_000);
            assert_eq!(conversion.spec["overrides"].as_array().unwrap().len(), 2);
        }
    }

    // ---- P155: fission, uncertainty and self-shielding keywords ----

    const RUN: &str = "FLUX 1E10\nTIME 1 HOURS ATOMS";

    fn hashed(path: &str, digit: &str) -> HashedFile {
        HashedFile {
            path: path.into(),
            sha256: digit.repeat(64),
        }
    }

    fn yield_file(path: &str, za: i32, liso: i32) -> FissionYieldFile {
        FissionYieldFile {
            file: hashed(path, "c"),
            parent_za: za,
            parent_liso: liso,
        }
    }

    /// Yield files for U-235, U-238 and Pu-241.
    fn ctx_fission() -> ImportContext {
        ImportContext {
            fission_yields: vec![
                yield_file("u235.endf", 92235, 0),
                yield_file("u238.endf", 92238, 0),
                yield_file("pu241.endf", 94241, 0),
            ],
            ..context()
        }
    }

    fn fission_deck(keywords: &str) -> String {
        deck_with(&format!("USEFISSION\n{keywords}\n{RUN}"))
    }

    #[test]
    fn p155_usefission_without_yield_files_names_the_option() {
        let conversion = convert(&fission_deck(""), &context()).unwrap();
        assert_eq!(entry_status(&conversion, "USEFISSION"), vec!["unsupported"]);
        assert!(entry_detail(&conversion, "USEFISSION").contains("--fission-yields"));
        assert!(conversion.spec.get("fission_yields").is_none());
        assert_eq!(conversion.unsupported, vec![("USEFISSION".to_string(), 5)]);
    }

    #[test]
    fn p155_usefission_with_files_maps_them_and_the_default_parents() {
        let conversion = convert(&fission_deck(""), &ctx_fission()).unwrap();
        let section = &conversion.spec["fission_yields"];
        assert_eq!(
            section["files"],
            json!([
                {"path": "u235.endf", "sha256": "c".repeat(64)},
                {"path": "u238.endf", "sha256": "c".repeat(64)},
                {"path": "pu241.endf", "sha256": "c".repeat(64)},
            ])
        );
        // U-235 and U-238 are default parents with a file; Pu-239 has none and is reported
        assert_eq!(section["parents"], json!({"include": ["U235", "U238"]}));
        assert!(section.get("yields").is_none());
        assert_eq!(entry_status(&conversion, "USEFISSION"), vec!["mapped"]);
        assert!(entry_detail(&conversion, "USEFISSION").contains("Pu239"));
        assert!(conversion.report.get("options_unused").is_none());
        // no default parent has a file: every supplied parent is excluded
        let only = ImportContext {
            fission_yields: vec![yield_file("pu241.endf", 94241, 0)],
            ..context()
        };
        let none = convert(&fission_deck(""), &only).unwrap();
        assert_eq!(
            none.spec["fission_yields"]["parents"],
            json!({"exclude": ["Pu241"]})
        );
    }

    #[test]
    fn p155_nofiss_and_cumfyld_set_the_yield_files_and_the_yield_set() {
        // NOFISS forces no yield files, whatever else the deck and the options say
        let off = convert(&fission_deck("NOFISS"), &ctx_fission()).unwrap();
        assert!(off.spec.get("fission_yields").is_none());
        assert_eq!(entry_status(&off, "NOFISS"), vec!["mapped"]);
        assert_eq!(entry_status(&off, "USEFISSION"), vec!["noted"]);
        assert_eq!(off.report["options_unused"], json!(["--fission-yields"]));
        // CUMFYLD selects the cumulative yields
        let cumulative = convert(&fission_deck("CUMFYLD"), &ctx_fission()).unwrap();
        assert_eq!(cumulative.spec["fission_yields"]["yields"], "cumulative");
        assert_eq!(entry_status(&cumulative, "CUMFYLD"), vec!["mapped"]);
        // without yield files it has nothing to select
        let bare = convert(&deck_with(&format!("CUMFYLD\n{RUN}")), &ctx_fission()).unwrap();
        assert!(bare.spec.get("fission_yields").is_none());
        assert_eq!(entry_status(&bare, "CUMFYLD"), vec!["noted"]);
        // a deck without USEFISSION leaves the supplied files unused and says so
        let unused = convert(&deck_with(RUN), &ctx_fission()).unwrap();
        assert!(unused.spec.get("fission_yields").is_none());
        assert_eq!(unused.report["options_unused"], json!(["--fission-yields"]));
    }

    #[test]
    fn p155_fisyield_counts_select_the_parents() {
        let parents = |keywords: &str| {
            let conversion = convert(&fission_deck(keywords), &ctx_fission()).unwrap();
            assert_eq!(entry_status(&conversion, "FISYIELD"), vec!["mapped"]);
            conversion.spec["fission_yields"]
                .get("parents")
                .cloned()
                .unwrap_or(Value::Null)
        };
        assert_eq!(parents("FISYIELD 1 PU241"), json!({"include": ["Pu241"]}));
        assert_eq!(
            parents("FISYIELD 2 U235 PU241"),
            json!({"include": ["U235", "Pu241"]})
        );
        assert_eq!(parents("FISYIELD -1 U238"), json!({"exclude": ["U238"]}));
        // zero: every parent with a yield file is excluded
        assert_eq!(
            parents("FISYIELD 0"),
            json!({"exclude": ["U235", "U238", "Pu241"]})
        );
        // named parents with no yield file are dropped and reported
        let dropped = convert(&fission_deck("FISYIELD 2 U235 PU239"), &ctx_fission()).unwrap();
        assert_eq!(
            dropped.spec["fission_yields"]["parents"],
            json!({"include": ["U235"]})
        );
        assert!(entry_detail(&dropped, "FISYIELD").contains("Pu239"));
        // an include list left empty excludes every parent; an exclude list left empty excludes none
        assert_eq!(
            parents("FISYIELD 1 AM242M"),
            json!({"exclude": ["U235", "U238", "Pu241"]})
        );
        assert_eq!(parents("FISYIELD -1 PU239"), Value::Null);
        // a count that disagrees with the list is refused
        let bad = convert(&fission_deck("FISYIELD 2 U235"), &ctx_fission()).unwrap();
        assert_eq!(entry_status(&bad, "FISYIELD"), vec!["unsupported"]);
        // without USEFISSION the selection has no effect
        let idle = convert(&deck_with(&format!("FISYIELD 0\n{RUN}")), &ctx_fission()).unwrap();
        assert_eq!(entry_status(&idle, "FISYIELD"), vec!["noted"]);
        assert!(idle.spec.get("fission_yields").is_none());
    }

    #[test]
    fn p155_fischoose_restricts_the_pathway_fission_parents() {
        let deck = deck_with(&format!("PATH 1 MN54\nFISCHOOSE 2 U235 PU239\n{RUN}"));
        let conversion = convert(&deck, &context()).unwrap();
        assert_eq!(
            conversion.spec["pathways"],
            json!({"targets": ["Mn54"], "fission_parents": ["U235", "Pu239"]})
        );
        assert_eq!(entry_status(&conversion, "FISCHOOSE"), vec!["mapped"]);
        // no pathways requested: nothing to restrict
        let bare = convert(&deck_with(&format!("FISCHOOSE 1 U235\n{RUN}")), &context()).unwrap();
        assert!(bare.spec.get("pathways").is_none());
        assert_eq!(entry_status(&bare, "FISCHOOSE"), vec!["noted"]);
        // the count must match the list
        let bad = convert(
            &deck_with(&format!("PATH\nFISCHOOSE 3 U235\n{RUN}")),
            &context(),
        )
        .unwrap();
        assert_eq!(entry_status(&bad, "FISCHOOSE"), vec!["unsupported"]);
        assert!(bad.spec["pathways"].get("fission_parents").is_none());
    }

    fn uncertain(keywords: &str) -> String {
        deck_with(&format!("UNCERT 2\n{keywords}\n{RUN}"))
    }

    #[test]
    fn p155_unctype_sets_the_uncertainty_channels() {
        for (kind, channels, status) in [
            ("1", json!(["cross_section_mf33"]), "mapped"),
            ("2", json!(["decay_constants"]), "approximated"),
            (
                "3",
                json!(["cross_section_mf33", "decay_constants"]),
                "mapped",
            ),
        ] {
            let conversion = convert(
                &uncertain(&format!("UNCTYPE {kind}")),
                &context_with_covariance(),
            )
            .unwrap();
            assert_eq!(
                conversion.spec["uncertainty"]["channels"], channels,
                "UNCTYPE {kind}"
            );
            assert_eq!(entry_status(&conversion, "UNCTYPE"), vec![status]);
        }
        // the default channel set stays implicit
        let plain = convert(&uncertain(""), &context_with_covariance()).unwrap();
        assert!(plain.spec["uncertainty"].get("channels").is_none());
        // no uncertainty block: the choice has no effect
        let none = convert(&uncertain("UNCTYPE 3"), &context()).unwrap();
        assert!(none.spec.get("uncertainty").is_none());
        assert_eq!(entry_status(&none, "UNCTYPE"), vec!["noted"]);
        let bad = convert(&uncertain("UNCTYPE 4"), &context_with_covariance()).unwrap();
        assert_eq!(entry_status(&bad, "UNCTYPE"), vec!["unsupported"]);
        // COVARIANCE is a library-phase collapse that ACTINV does not need
        let covariance = convert(&uncertain("COVARIANCE"), &context_with_covariance()).unwrap();
        assert_eq!(entry_status(&covariance, "COVARIANCE"), vec!["noted"]);
    }

    #[test]
    fn p155_sensitivity_sigma_asks_for_the_ranked_variance_shares() {
        let deck =
            uncertain("SENSITIVITY SIGMA 0.8 1 1\nFE56 FE57\nFE57\nMCSAMPLE 1 100\nMCSEED 7");
        let conversion = convert(&deck, &context_with_covariance()).unwrap();
        assert_eq!(conversion.spec["uncertainty"]["voi"], json!({"top": 10}));
        assert_eq!(
            entry_status(&conversion, "SENSITIVITY"),
            vec!["approximated"]
        );
        assert_eq!(entry_status(&conversion, "MCSAMPLE"), vec!["noted"]);
        assert_eq!(entry_status(&conversion, "MCSEED"), vec!["noted"]);
        // SENSITIVITY alone brings its own uncertainty block
        let alone = convert(
            &deck_with(&format!("SENSITIVITY SIGMA 0.8 0 0\n{RUN}")),
            &context_with_covariance(),
        )
        .unwrap();
        assert_eq!(alone.spec["uncertainty"]["voi"], json!({"top": 10}));
        assert_eq!(alone.spec["uncertainty"]["covariance"]["path"], "cov.npz");
        // without a covariance sidecar there is nothing to rank: the sampling keywords go with it
        let refused = convert(&deck, &context()).unwrap();
        assert!(refused.spec.get("uncertainty").is_none());
        assert_eq!(entry_status(&refused, "SENSITIVITY"), vec!["unsupported"]);
        assert_eq!(entry_status(&refused, "MCSAMPLE"), vec!["unsupported"]);
        assert_eq!(entry_status(&refused, "MCSEED"), vec!["unsupported"]);
        // decay-constant sensitivities are not available
        let lambda = convert(
            &uncertain("SENSITIVITY LAMBDA 0.8 1 1\nFE56 FE57\nFE57\nMCSAMPLE 1 100"),
            &context_with_covariance(),
        )
        .unwrap();
        assert!(lambda.spec["uncertainty"].get("voi").is_none());
        assert_eq!(entry_status(&lambda, "SENSITIVITY"), vec!["unsupported"]);
        assert_eq!(entry_status(&lambda, "MCSAMPLE"), vec!["unsupported"]);
        // sampling settings with no SENSITIVITY configure nothing
        let seed = convert(&uncertain("MCSEED 12345"), &context_with_covariance()).unwrap();
        assert_eq!(entry_status(&seed, "MCSEED"), vec!["unsupported"]);
    }

    #[test]
    fn p155_error_becomes_a_cross_section_uncertainty_override() {
        let deck = uncertain("ERROR 2\nFE56 FE57 0.1\nFE56 FISSION -1");
        let conversion = convert(&deck, &context_with_covariance()).unwrap();
        assert_eq!(
            conversion.spec["overrides"],
            json!([{"nuclide": "Fe56", "kind": "cross_section_uncertainty", "mt": 102, "value": 0.1}])
        );
        assert_eq!(entry_status(&conversion, "ERROR"), vec!["approximated"]);
        assert!(entry_detail(&conversion, "ERROR").contains("relative standard uncertainty"));
        assert!(conversion.spec["uncertainty"].get("covariance").is_some());
        // -1 asks for the library value, which the covariance already is
        let library = convert(
            &uncertain("ERROR 1\nFE56 FE57 -1.0"),
            &context_with_covariance(),
        )
        .unwrap();
        assert!(library.spec.get("overrides").is_none());
        assert_eq!(entry_status(&library, "ERROR"), vec!["noted"]);
        // a fission reaction is MT 18
        let fission = convert(
            &uncertain("ERROR 1\nU235 0 0.05"),
            &context_with_covariance(),
        )
        .unwrap();
        assert_eq!(fission.spec["overrides"][0]["mt"], 18);
        // no uncertainty block, or one without the cross-section channel: refused, nothing emitted
        let without = convert(&uncertain("ERROR 1\nFE56 FE57 0.1"), &context()).unwrap();
        assert!(without.spec.get("overrides").is_none());
        assert_eq!(entry_status(&without, "ERROR"), vec!["unsupported"]);
        let decay_only = convert(
            &uncertain("UNCTYPE 2\nERROR 1\nFE56 FE57 0.1"),
            &context_with_covariance(),
        )
        .unwrap();
        assert!(decay_only.spec.get("overrides").is_none());
        assert_eq!(entry_status(&decay_only, "ERROR"), vec!["unsupported"]);
        // a reaction ACTINV cannot name, or a malformed card
        for bad in [
            "ERROR 1\nFE56 NI56 0.1",
            "ERROR 2\nFE56 FE57 0.1",
            "ERROR 1\nFE56 FE57 -0.5",
        ] {
            let conversion = convert(&uncertain(bad), &context_with_covariance()).unwrap();
            assert_eq!(
                entry_status(&conversion, "ERROR"),
                vec!["unsupported"],
                "{bad}"
            );
        }
    }

    fn shielded(keywords: &str) -> String {
        deck_with(&format!("DENSITY 7.874\n{keywords}\n{RUN}"))
    }

    fn ctx_resonance() -> ImportContext {
        ImportContext {
            resonance: vec![hashed("fe56.endf", "d"), hashed("fe57.endf", "e")],
            ..context()
        }
    }

    fn ctx_table() -> ImportContext {
        ImportContext {
            self_shielding_table: Some(hashed("shield.json", "f")),
            ..context()
        }
    }

    #[test]
    fn p155_ssfgeometry_with_resonance_files_maps_the_geometry() {
        let foil = convert(&shielded("SSFGEOMETRY 1 0.01"), &ctx_resonance()).unwrap();
        assert_eq!(
            foil.spec["self_shielding"],
            json!({"geometry": {
                "shape": "foil",
                "length_cm": 0.01,
                "density_g_cm3": 7.874,
                "resonance_files": [
                    {"path": "fe56.endf", "sha256": "d".repeat(64)},
                    {"path": "fe57.endf", "sha256": "e".repeat(64)},
                ],
            }})
        );
        assert_eq!(entry_status(&foil, "SSFGEOMETRY"), vec!["mapped"]);
        assert!(foil.report["required_additions"]
            .as_array()
            .unwrap()
            .is_empty());
        // wire and sphere; the cylinder also takes a height
        for (kind, shape) in [("2", "wire"), ("3", "sphere")] {
            let conversion = convert(
                &shielded(&format!("SSFGEOMETRY {kind} 0.5")),
                &ctx_resonance(),
            )
            .unwrap();
            assert_eq!(
                conversion.spec["self_shielding"]["geometry"]["shape"],
                shape
            );
        }
        let cylinder = convert(&shielded("SSFGEOMETRY 4 0.5 2.0"), &ctx_resonance()).unwrap();
        let geometry = &cylinder.spec["self_shielding"]["geometry"];
        assert_eq!(geometry["shape"], "cylinder");
        assert_eq!(geometry["height_cm"], 2.0);
        // SSFCHOOSE restricts it; SSFMASS and SSFFUEL give a composition ACTINV does not read
        let chosen = convert(
            &shielded("SSFCHOOSE 2 0 FE56 FE\nSSFMASS 1.0 1\n FE 100\nSSFGEOMETRY 1 0.01"),
            &ctx_resonance(),
        )
        .unwrap();
        assert_eq!(
            chosen.spec["self_shielding"]["geometry"]["choose"],
            json!(["Fe56", "Fe"])
        );
        assert_eq!(entry_status(&chosen, "SSFCHOOSE"), vec!["mapped"]);
        assert_eq!(entry_status(&chosen, "SSFMASS"), vec!["approximated"]);
        let fuel = convert(
            &shielded("SSFFUEL 1\nFE56 1.0E22\nSSFGEOMETRY 1 0.01"),
            &ctx_resonance(),
        )
        .unwrap();
        assert_eq!(entry_status(&fuel, "SSFFUEL"), vec!["approximated"]);
        // no DENSITY: the section is emitted and the density is listed as missing
        let bare = convert(
            &deck_with(&format!("SSFGEOMETRY 1 0.01\n{RUN}")),
            &ctx_resonance(),
        )
        .unwrap();
        let geometry = &bare.spec["self_shielding"]["geometry"];
        assert!(geometry.get("density_g_cm3").is_none());
        assert_eq!(
            bare.report["required_additions"][0]["field"],
            "self_shielding.geometry.density_g_cm3"
        );
    }

    #[test]
    fn p155_self_shielding_without_its_option_names_it() {
        let geometry = convert(
            &shielded("SSFGEOMETRY 1 0.01\nSSFCHOOSE 1 0 FE\nSSFFUEL 1\nFE56 1.0E22"),
            &context(),
        )
        .unwrap();
        assert!(geometry.spec.get("self_shielding").is_none());
        for keyword in ["SSFGEOMETRY", "SSFCHOOSE", "SSFFUEL"] {
            assert_eq!(
                entry_status(&geometry, keyword),
                vec!["unsupported"],
                "{keyword}"
            );
            assert!(
                entry_detail(&geometry, keyword).contains("--resonance"),
                "{keyword}"
            );
        }
        let table = convert(
            &shielded("PROBTABLE 1 0\nSSFDILUTION 1\nFE56 1\n300 100.0"),
            &context(),
        )
        .unwrap();
        assert!(table.spec.get("self_shielding").is_none());
        for keyword in ["PROBTABLE", "SSFDILUTION"] {
            assert_eq!(
                entry_status(&table, keyword),
                vec!["unsupported"],
                "{keyword}"
            );
            assert!(
                entry_detail(&table, keyword).contains("--self-shielding-table"),
                "{keyword}"
            );
        }
        // a selection with no request and no option behind it
        let stray = convert(&shielded("SSFCHOOSE 1 0 FE"), &context()).unwrap();
        assert_eq!(entry_status(&stray, "SSFCHOOSE"), vec!["unsupported"]);
        // an option the deck never uses is reported as unused
        let idle = convert(&shielded("SSFCHOOSE 1 0 FE"), &ctx_resonance()).unwrap();
        assert_eq!(entry_status(&idle, "SSFCHOOSE"), vec!["noted"]);
        assert_eq!(idle.report["options_unused"], json!(["--resonance"]));
    }

    #[test]
    fn p155_ssfgeometry_refuses_what_actinv_cannot_shield() {
        // a target type the manual does not define
        let kind = convert(&shielded("SSFGEOMETRY 7 0.01"), &ctx_resonance()).unwrap();
        assert_eq!(entry_status(&kind, "SSFGEOMETRY"), vec!["unsupported"]);
        assert!(kind.spec.get("self_shielding").is_none());
        // a cylinder without its height, a nonpositive length
        for bad in ["SSFGEOMETRY 4 0.5", "SSFGEOMETRY 1 0.0"] {
            let conversion = convert(&shielded(bad), &ctx_resonance()).unwrap();
            assert_eq!(
                entry_status(&conversion, "SSFGEOMETRY"),
                vec!["unsupported"],
                "{bad}"
            );
        }
        // FISPACT-II does not allow both models, and ACTINV takes one
        let both = ImportContext {
            self_shielding_table: Some(hashed("shield.json", "f")),
            ..ctx_resonance()
        };
        let conversion = convert(&shielded("SSFGEOMETRY 1 0.01\nPROBTABLE 1 0"), &both).unwrap();
        assert!(conversion.spec.get("self_shielding").is_none());
        assert_eq!(
            entry_status(&conversion, "SSFGEOMETRY"),
            vec!["unsupported"]
        );
        assert_eq!(entry_status(&conversion, "PROBTABLE"), vec!["unsupported"]);
        // a charged-particle problem has no self-shielding
        let proton = ImportContext {
            flux: Some(LoadedFlux {
                values: vec![1.0; 162],
                ..flux709()
            }),
            ..ctx_resonance()
        };
        let deck = format!("PROJECTILE 3\n{}", shielded("SSFGEOMETRY 1 0.01"));
        let conversion = convert(&deck, &proton).unwrap();
        assert!(conversion.spec.get("self_shielding").is_none());
        assert_eq!(
            entry_status(&conversion, "SSFGEOMETRY"),
            vec!["unsupported"]
        );
    }

    #[test]
    fn p155_probtable_and_ssfdilution_with_a_table_map_the_table() {
        let conversion = convert(&shielded("PROBTABLE 1 0"), &ctx_table()).unwrap();
        assert_eq!(
            conversion.spec["self_shielding"],
            json!({"table": {"path": "shield.json", "sha256": "f".repeat(64)}})
        );
        assert_eq!(entry_status(&conversion, "PROBTABLE"), vec!["approximated"]);
        // FISPACT-II lists the highest-energy group first (group 1); ACTINV's index is 0-based and
        // ascending in energy, so group g of the 709 is index 709 - g
        let dilution = convert(
            &shielded("PROBTABLE 1 0\nSSFDILUTION 2\nFE56 2\n300 100.0 301 80.0\nFE57 1\n709 2.5"),
            &ctx_table(),
        )
        .unwrap();
        assert_eq!(
            dilution.spec["self_shielding"]["dilution_overrides"],
            json!({"Fe56": {"409": 100.0, "408": 80.0}, "Fe57": {"0": 2.5}})
        );
        assert_eq!(entry_status(&dilution, "SSFDILUTION"), vec!["mapped"]);
        assert!(entry_detail(&dilution, "SSFDILUTION").contains("709 - g"));
        // group 1 is the top group
        let top = convert(
            &shielded("PROBTABLE 1 0\nSSFDILUTION 1\nFE56 1\n1 10.0"),
            &ctx_table(),
        )
        .unwrap();
        assert_eq!(
            top.spec["self_shielding"]["dilution_overrides"],
            json!({"Fe56": {"708": 10.0}})
        );
        // SSFDILUTION alone selects the table mode
        let alone = convert(&shielded("SSFDILUTION 1\nFE56 1\n300 100.0"), &ctx_table()).unwrap();
        assert!(alone.spec["self_shielding"].get("table").is_some());
        // a group outside the library, a malformed card
        for bad in [
            "SSFDILUTION 1\nFE56 1\n710 10.0",
            "SSFDILUTION 2\nFE56 1\n300 10.0",
        ] {
            let conversion =
                convert(&shielded(&format!("PROBTABLE 1 0\n{bad}")), &ctx_table()).unwrap();
            assert_eq!(
                entry_status(&conversion, "SSFDILUTION"),
                vec!["unsupported"],
                "{bad}"
            );
            assert!(conversion.spec["self_shielding"]
                .get("dilution_overrides")
                .is_none());
        }
        // SSFCHOOSE names do not restrict a table
        let chosen = convert(&shielded("PROBTABLE 1 0\nSSFCHOOSE 1 0 FE56"), &ctx_table()).unwrap();
        assert_eq!(entry_status(&chosen, "SSFCHOOSE"), vec!["approximated"]);
    }

    #[test]
    fn p155_misc_keywords_are_noted_or_refused_with_their_reasons() {
        for keyword in ["XSTHRESHOLD 1.0E-12", "DEPLETION 1 FE56", "COVARIANCE"] {
            let conversion = convert(&deck_with(&format!("{keyword}\n{RUN}")), &context()).unwrap();
            let name = keyword.split(' ').next().unwrap();
            assert_eq!(entry_status(&conversion, name), vec!["noted"], "{name}");
        }
        for keyword in [
            "READSF",
            "USESPALLATION 100.0 1\nPB204",
            "USEXSEXTRA",
            "IRON",
        ] {
            let conversion = convert(&deck_with(&format!("{keyword}\n{RUN}")), &context()).unwrap();
            let name = keyword.split(['\n', ' ']).next().unwrap();
            assert_eq!(
                entry_status(&conversion, name),
                vec!["unsupported"],
                "{name}"
            );
        }
        let iron = convert(&deck_with(&format!("IRON\n{RUN}")), &context()).unwrap();
        assert!(entry_detail(&iron, "IRON").contains("2023"));
        assert!(!entry_detail(&iron, "IRON").contains("background removal"));
        // RESULT is noted beside a deck that runs its own inventory, refused when it would be the
        // only one
        let own = convert(
            &deck_with(&format!("{RUN}\nPATH 1 MN54\nRESULT 1\nFE56 1.0E10")),
            &context(),
        )
        .unwrap();
        assert_eq!(entry_status(&own, "RESULT"), vec!["noted"]);
        let first = convert(
            &deck_with(&format!("RESULT 1\nFE56 1.0E10\n{RUN}")),
            &context(),
        )
        .unwrap();
        assert_eq!(entry_status(&first, "RESULT"), vec!["unsupported"]);
    }

    #[test]
    fn p155_a_deck_without_the_new_keywords_converts_as_before() {
        // the options change nothing for a deck that does not use them
        let plain = convert(&deck_with(RUN), &context()).unwrap();
        let supplied = ImportContext {
            fission_yields: ctx_fission().fission_yields,
            self_shielding_table: ctx_table().self_shielding_table,
            resonance: ctx_resonance().resonance,
            damage: Some(hashed("damage.json", "a")),
            ..context()
        };
        let with_options = convert(&deck_with(RUN), &supplied).unwrap();
        assert_eq!(plain.spec, with_options.spec);
        assert!(plain.report.get("options_unused").is_none());
        assert_eq!(
            with_options.report["options_unused"],
            json!(["--fission-yields", "--resonance", "--self-shielding-table"])
        );
    }
}
