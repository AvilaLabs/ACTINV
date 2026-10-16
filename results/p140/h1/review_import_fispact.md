# H1 adversarial review: `crates/actinv-cli/src/fispact_import.rs` (P132 + P138)

Read-only review. Nothing was built or run. Every "predicted" result below is derived by tracing the code; none was
executed. Real decks read (data only, nothing executed):
`~/nuclear-data/conderc-fission/fission/Fispact-II/Inputs/{U235pulse.i,U2352E4s.i}`,
`~/nuclear-data/conderc-fns/fns/Ag/*.i` (+ files file, flux file), `~/nuclear-data/ext-decks/pypact/reference/test{,2,3,4,5}.i`,
`pypact/input/keywords.py` (FISPACT-II keyword inventory), and a scripted first-token scan of all 141 `*.i` under
`~/nuclear-data` against the importer's `KEYWORDS` table.

Line numbers refer to the working tree at review time.

---------------------------------------------------------------------------------------------------------------------

## Ranked findings

### F1. defect (high). Valid FISPACT-II keywords that ship in real decks are reported as "not a FISPACT-II keyword" (`unsupported`), so `--strict` fails

- Where: `resolve_keyword` L308-329 (`upper.chars().count() < 4 -> None`), `KEYWORDS` L168-263, `apply` L1019-1026.
- Description. Two independent gaps.
  1. Abbreviations shorter than four characters are never resolved. The conderc-fission decks that ship with
     FISPACT-II (`U235pulse.i` and `U2352E4s.i`, in `Fispact-II/Inputs/`) use `TOL 0 1E-2 1E-6`. The importer's own docs
     say "only the leading characters matter", and the same deck with `TOLER` is `noted`. `TOL` is resolved to nothing,
     becomes an unknown keyword, and is logged `unsupported`.
  2. The table omits real keywords. Against the FISPACT-II inventory in `pypact/input/keywords.py` it lacks `MCSEED`
     (a current keyword) and the deprecated spellings `NOHEAD`/`NOHEADER` (in both shipped decks), `AINPUT`, `ARRAY`,
     `COLLAPSE`, `CONV`, `DOMINANT`, `EAFVERSION`, `ENFA`, `LEVEL`, `LINA`, `LOOPS`, `NEWFILE`, `SEQNUMBER`,
     `SEQUENTIAL`, `TAPA`. Several of these change meaning (`COLLAPSE` = `GETXS 1`, `ARRAY` = `GETDECAY 0`,
     `AINPUT` = `GETXS 0 GETDECAY 0`), the rest are print or no-op switches. All are mis-reported as misspellings.
- Reproduction (deck text, verbatim from the shipped fission deck):
  ```
  CLOBBER
  NOHEAD
  GETXS 0
  GETDECAY 0
  FISPACT
  *Uranium 235 fission pulse
  FUEL 1
    U235 1.0E+24
  TOL 0 1E-2 1E-6
  FLUX 1.0E+22
  TIME 1.0E-06
  ATOMS
  END
  ```
  Expected: `NOHEAD` noted (header suppression), `TOL` noted (tolerances), 0 unsupported from these two.
  Predicted actual: two entries `unsupported` with detail "not a FISPACT-II keyword; the importer cannot tell what it
  was meant to do"; `counts.unsupported >= 2`; `--strict` exits 1 with "NOHEAD (line 2), TOL (line 11)".
  The FNS decks are unaffected (they use `TOLER`), which is why the G2 corpus gate did not see this.
- Confidence: high for the code path; high that `TOL` and `NOHEAD` are accepted by FISPACT-II (they are in its own
  distribution inputs).
- Fix: accept unique prefixes of 3 or more characters (END is exact-matched first, so ENDPULSE is not at risk; check
  uniqueness with the full table), add `NOHEAD`/`NOHEADER`, `MCSEED`, and the deprecated set as `noted` (or map
  `COLLAPSE`/`ARRAY`/`AINPUT` to the `GETXS`/`GETDECAY` handling). Say "unknown to this importer" rather than "not a
  FISPACT-II keyword", and add a regression test using these two shipped decks.

### F2. defect (high). An unrecognised keyword that follows a `Words`-arity keyword is swallowed into its arguments and never reported as a keyword

- Where: `take_words` L402-412 (stops only at a word that `resolve_keyword` recognises); consumers `apply_over` L1660-1757
  (stray list), `apply_pathways` L1830-1865 (`unread`), `apply_power` L1466-1496 (`mts`), `partition_pairs` L880-920,
  `apply_bremsstrahlung` L1954-2016.
- Description. Any keyword the importer does not know (F1 cases, a typo, a keyword added in a later FISPACT-II release)
  that directly follows `OVER`, `PATH`, `ROUTES`, `POWER`, `PARTITION`, `BREMSSTRAHLUNG`, `ERROR`, `FISYIELD`, `FISCHOOSE`,
  `DEPLETION`, `SENSITIVITY`, `RESULT`, `ATDISPEN`, `EXTRACTXS`, `SSF*`, `USESPALLATION` is consumed as an argument word.
  It gets no entry of its own and `--strict` cannot see it. In `OVER`/`PATH` it surfaces only as prose in the parent's
  `detail` ("not interpreted: ..."), status `approximated`. For `POWER` it is silently appended to the MT list. For
  `PARTITION` and `BREMSSTRAHLUNG` it makes a valid keyword `unsupported` for the wrong reason (odd word count,
  "non-nuclide").
- Reproduction:
  ```
  FISPACT
  * t
  MASS 1E-3 1
   FE 100
  PATH Co58
  NOHEAD
  FLUX 1E10
  TIME 1 HOURS ATOMS
  END
  ```
  Expected: `PATH` approximated, `NOHEAD` a separate entry (so strict reacts to it).
  Predicted actual: one `PATH` entry (`approximated`, "the numeric or other argument(s) 'NOHEAD' are not interpreted"),
  no `NOHEAD` entry, `counts.unsupported == 0`, `--strict` passes.
  Worse variant: `PARTITION 1 Fe 0.5` followed by an unknown keyword makes the whole PARTITION `unsupported`
  ("expected element/fraction pairs") although the physics lines are valid.
- Confidence: high (direct from `take_words`).
- Fix: in `take_words` stop at any token that is alphabetic, not a number and not a plausible argument (for the nuclide
  lists: stop at the first word `fispact_nuclide`/element lookup rejects that is not a known sub-keyword such as ACROSS),
  or emit a distinct report entry for every word an arity routine could not interpret and count it as unsupported.

### F3. defect (medium-high on reporting, medium on physics). A `TIME` that no output keyword runs is dropped, including its flux, while its own entry stays `mapped`

- Where: `Pending` replace in the TIME handler L1113-1126; end-of-deck handling L2128-2137; documented in
  `docs/guide/fispact-migration.md` L40.
- Description. The importer models `TIME` as deferred: it runs only at the next `ATOMS`/`STEP`/`SPECTRUM`, and a second
  `TIME` before that replaces the first. The flux used is `self.current` at the moment the output keyword is read
  (L1451), not at the moment `TIME` was read. The consequences:
  1. The replaced interval vanishes from the schedule, but its own report entry still says `mapped` / "runs at the next
     ATOMS, STEP or SPECTRUM". The loss is recorded only as a parenthetical on the replacing `TIME`'s entry. A trailing
     unrun `TIME` is `noted`. `--strict` never sees either.
  2. `FLUX a; TIME t1; FLUX b; TIME t2; ATOMS` runs one step of `t2` at `b` (never `t1` at `a`). If FISPACT-II runs the
     calculation at each `TIME` and `ATOMS` only prints (the pypact reader in `pypact/input/inputdata.py` L496-540
     treats every `TIME` as a step, and `reference/test.i` is written exactly this way), the importer silently deletes
     an irradiation interval.
- Reproduction (the pypact reference deck `test.i` core):
  ```
  FISPACT
  * t
  MASS 1.0 3
   Ti 80.0
   Fe 14.8
   Cr 5.2
  FLUX 1100000000000000.0
  TIME 300.0 SECS
  FLUX 42.0
  TIME 200.0 SECS
  ATOMS
  END
  ```
  Expected (if each TIME executes): 2 steps, 300 s at 1.1e15 then 200 s at 42. Predicted actual: 1 step (200 s,
  multiplier 42/1.1e15 = 3.8e-14); the 300 s irradiation at 1.1e15 is absent; first `TIME` entry `mapped`.
  Sharper: `FLUX 1E14 / TIME 1 YEARS / FLUX 0 / TIME 1 DAYS ATOMS` yields a spec with only a cooling step, no irradiation at
  all, `spectrum.total` still set, and zero strict findings.
- Confidence: high that the dropped interval is reported as `mapped` (a reporting-honesty defect on any reading); about
  50% that FISPACT-II semantics differ (evidence both ways: the UKAEA FNS tutorial comment "ATOMS ... tells F-II to solve
  rate equations" supports deferral; pypact's reader and `test.i` suggest immediate execution). The migration guide
  should state the assumption and cite the manual section.
- Fix: at minimum mark the replaced and trailing intervals `unsupported` (strict-visible) with the lost flux and
  duration in the detail; better, confirm the semantics against a FISPACT-II `.out` of a two-TIME-one-ATOMS deck and
  either accumulate the intervals or keep them as separate steps.

### F4. defect (medium-high). `OVER` after the first `TIME` is applied to the whole run and reported `mapped`

- Where: `apply_over` L1660-1757 has no phase check (compare `NOSTABLE` L1138-1149 and `DOSE` L1887-1900, which say
  "applied from this point"); `OVER` is listed as an inventory-phase keyword in pypact `INVENTORY_KEYWORDS`.
- Description. `OVER` replaces a collapsed cross section or half-life. FISPACT-II applies it to the steps that follow.
  The importer appends an `overrides` item that `actinv` applies from t = 0. `ALAM` (`mapped`) and `ACROSS`
  (`approximated` for branching, not for timing) never mention it. Any irradiation step already scheduled uses the
  overridden value.
- Reproduction:
  ```
  FISPACT
  * t
  MASS 1E-3 1
   FE 100
  FLUX 1E10
  TIME 1 YEARS ATOMS
  OVER Co58
   ALAM 100 DAYS
  FLUX 0
  TIME 1 DAYS ATOMS
  END
  ```
  Expected: `ALAM` flagged as taking effect only after step 1 (approximated/unsupported). Predicted actual:
  `overrides: [{Co58 half_life 100 d}]` global, `ALAM` status `mapped`, `OVER` mapped, strict passes; the 1-year
  irradiation already uses the 100 d half-life.
- Confidence: high on the code; medium-high on FISPACT-II applying OVER forward-only (it is a valid inventory-phase
  keyword).
- Fix: when `cmd.phase == Phase::Inventory && !self.steps.is_empty()` mark the sub-entries `approximated` ("applies to the
  whole run") or `unsupported` and do not emit the override; step-scoped overrides are not in the spec.

### F5. robustness (high). Nested/huge `PULSE` counts hang the importer (no step cap on step-free bodies); deep nesting overflows the stack

- Where: count bound `<= 1.0e9` L483; `run_nodes` L1004-1007; `MAX_STEPS` is checked only in `apply_trigger` L1441.
- Description. A `PULSE` body that adds no step (no pending `TIME`, or only `FLUX`/`ZERO`) never reaches the 100000-step
  guard, so `PULSE 1000000000` costs 1e9 iterations of `run_nodes`, and two nested loops cost 1e18. Independently,
  `parse_nodes` recurses once per `PULSE` with no depth limit, so ~10^5 nested `PULSE 1` lines (about 300 kB) can overflow
  the stack (abort, not a clean error). `run_nodes` recurses likewise.
- Reproduction:
  ```
  FISPACT
  * t
  MASS 1E-3 1
   FE 100
  PULSE 1000000000
   PULSE 1000000000
    FLUX 1E10
   ENDPULSE
  ENDPULSE
  FLUX 1E10
  TIME 1 HOURS ATOMS
  END
  ```
  Expected: error or bounded run. Predicted: CPU-bound effectively unbounded loop (single level ~1e9 iterations, tens of
  seconds). For depth: 100000 repetitions of `PULSE 1` then one `TIME 1 ATOMS` and 100000 `ENDPULSE`: stack overflow.
- Confidence: high for the loop; medium for the exact depth at which the stack overflows.
- Fix: count loop iterations (product of nested counts) against a work budget before expanding; cap nesting depth
  (e.g. 64) with a clean error; or compute `steps_per_iteration` once and bail when `count * steps > MAX_STEPS`.

### F6. defect (medium). Keywords inside `PULSE 0` produce no report entries

- Where: `run_nodes` L996-1008 (`for repeat in 0..*count` never runs the body when `count == 0`).
- Description. The parser accepts `PULSE 0` (L483 allows 0). The body is parsed but never interpreted, so none of its
  keywords is recorded, including unknown or unsupported ones. This breaks the "nothing is dropped silently" contract
  and `--strict`.
- Reproduction:
  ```
  FISPACT
  * t
  MASS 1E-3 1
   FE 100
  PULSE 0
   SSFGEOMETRY 1 0.5
   FROBNICATE
  ENDPULSE
  FLUX 1E10
  TIME 1 HOURS ATOMS
  END
  ```
  Expected: `SSFGEOMETRY` and `FROBNICATE` listed (or the whole block marked unsupported). Predicted: only a `PULSE`
  entry ("zero repetitions: the loop body is skipped"); `counts.unsupported == 0`.
- Confidence: high.
- Fix: run the body once with `record = true` and a "dry" flag that discards schedule effects, or log a per-keyword
  entry from the parse tree for the skipped body.

### F7. defect (medium). Stale `noted` / "resolved when the deck ends" entries when a deferred keyword repeats, and `None` indices in non-recorded loop iterations clobber settled ones

- Where: `UNCERTAINTY` L1188 (`self.uncert_entry = self.log(..)`), `apply_rebin` L1797-1816 (`rebin.* = Some((.., index))`),
  `settle_*` consumers L2039-2095, L2366-2446.
- Description. `UNCERTAINTY`, `GRPCONVERT`, `CNVTYPE`, `LIMGRP` log a placeholder entry (`noted`, "resolved when the deck
  ends") and keep only the latest index. (a) A second occurrence overwrites the index, so the first entry keeps the
  placeholder text and status forever (the report then claims "noted" for a keyword whose value was replaced; for
  `UNCERTAINTY 2 ... UNCERTAINTY 0` the first one never says it was superseded). (b) Inside a `PULSE` body (repeats run with
  `recording = false`, `log` returns `None`) the assignment from iteration 2 replaces the real index with `None`, so the
  entry is never settled (stays `noted`).
- Reproduction: `UNCERTAINTY 2` ... `UNCERTAINTY 0` in one deck (default library). Expected: first entry says superseded
  or the last-wins rule; predicted: first remains "resolved when the deck ends" / `noted`.
- Confidence: high on the code; low practical impact (`UNCERTAINTY` in a pulse body is unusual).
- Fix: store the index only when `Some`, and settle all indices (keep a Vec) or mark superseded ones.

### F8. defect (medium-low). `DOSE 1` after `DOSE 2` deletes the point source for the whole run, status `noted`

- Where: `apply_dose` L1875-1882 (`self.photon.point_source_m = None`).
- Description. A later `DOSE 1` erases an earlier `DOSE 2 d` retroactively and is logged as noting "slab contact dose is
  the default", while the earlier `DOSE 2` entry stays `mapped`. The spec and the entries disagree.
- Reproduction: `DOSE 2 1.0` ... (steps) ... `DOSE 1`. Predicted: no `photon.point_source` in the spec, `DOSE 2`
  entry still `mapped` ("photon.point_source.distance_m = 1").
- Confidence: high on code; low frequency.
- Fix: treat a changing DOSE mode after steps as `approximated` ("applies to the whole run") and keep the first request,
  or mark the earlier entry superseded.

### F9. robustness (medium-low). Non-UTF-8 deck, BOM, bare-CR line ends

- Where: `run` L2831 (`read_to_string`), `lex` L68 (`str::lines`), `lex` L72 (`trim_start().strip_prefix('*')`).
- Description. Decks written on Windows or with Latin-1 comments (units like a micro sign inside `<< >>`) fail with
  "stream did not contain valid UTF-8" and no report. A UTF-8 BOM before `FISPACT`/`<<`/`*` is not whitespace, so the
  first token becomes `\u{feff}FISPACT` and is `unsupported`, and a BOM before a `*` title line defeats the star rule.
  A deck with CR-only line endings is a single line, so `*` text lines and line numbers are wrong.
- Reproduction: save any deck with a `0xB5` byte in a `<< >>` comment; predicted: `cannot read DECK: stream did not
  contain valid UTF-8`. Expected: import proceeds (comments are ignorable).
- Confidence: high.
- Fix: `String::from_utf8_lossy`, strip a leading U+FEFF, split on `\r`/`\r\n`/`\n`.

### F10. robustness (low-medium). A `fluxes` entry in the files file that does not resolve is fatal even for a deck that never irradiates

- Where: `run` L2866-2876.
- Description. A cooling-only or decay-only deck (valid problem) with a `files` file that names a missing flux file fails
  with "the fluxes file ... was not found" before the deck is read. Files files copied from a cluster often name absolute
  paths that do not exist locally.
- Reproduction: files file `fluxes /nonexistent/fluxes`, deck with only `FLUX 0` steps; predicted: error. Expected: report
  the missing file only if an irradiating step needs it.
- Confidence: high. Fix: defer the error until `finish` knows whether a spectrum is needed (the existing "irradiates but no
  FLUXES file" message already covers it).

### F11. robustness (low). `--strict` failure leaves a stale `OUT.json`; `OUT == DECK` overwrites the deck

- Where: `run` L2920-2934.
- Description. On a strict failure the report is written but a previous `OUT.json` from an earlier non-strict run is left
  in place, so a script that ignores the exit code reads an old spec as current. If `OUT` is the same path as `DECK` the
  deck is overwritten (the report goes to `DECK.import.json`).
- Fix: remove or rename any existing `OUT` when strict fails; refuse `OUT == DECK` (canonicalise both).
- Confidence: high; low impact.

### F12. documentation (low-medium). Several FISPACT-II behaviours are asserted without a source, or contradict the reference outputs

- `ZERO`: the code says the schedule "carries elapsed time" (L1075). In the pypact reference `test31.out` the elapsed time
  printed after `ZERO` restarts (interval 3 "ELAPSED TIME IS 1.000 m" after a 1-year irradiation, "COOLING TIME IS
  6.0000E+01 SECS"). The G3 check compares interval durations, which hold, but any consumer comparing absolute times to
  FISPACT output will be off; say so. The claim "FISPACT-II allows ZERO once; a repeat only keeps the flux at zero"
  (L1073) is not sourced, and FISPACT-II refuses irradiation after `ZERO` while the importer silently accepts `ZERO`
  then `FLUX 1e14` (L1061, L1080).
- `MASS` percentages not summing to 100 (L1337-1341): the detail says "the same reading as FISPACT-II" and the spec does
  not renormalise (`composition.rs` L233). I could not confirm the FISPACT-II reading from the repository; mark as
  unverified.
- `MIND`: the shipped tutorial deck comments "1e5 atoms is the default" (`ext-decks/pypact/reference/test4.i`), so a
  deck with no `MIND` has an implicit 1e5 threshold that the importer neither maps nor mentions.
- `PULSE n`: neither the guide nor the code says whether n is the total repeat count or the count of repeats after the
  first execution (`test` asserts n executions). State the FISPACT-II reading and its source.
- `GRPCONVERT a b` arguments are ignored and the output structure is forced to the library's; the detail says so only when
  numbers were present, but never states what happens when the second number differs from the library group count.
- Isotopic abundances: `MASS` composition resolves through ACTINV's natural-abundance table, FISPACT-II's differs slightly;
  the import report does not mention it.
- Confidence: medium for the first (supported by `test31.out`), low-medium for the others (unverifiable here).

### F13. robustness (low). Time-unit tokens: only `SEC*`, `MIN*`, `HOU*`/`HR*`, `DAY*`, `YEAR`/`YR*` are recognised

- Where: `time_unit` L138-149.
- Description. Single-letter or unusual spellings (`s`, `m`, `h`, `d`, `y`, `a`) are not units, so after `TIME 5 y` the
  `y` becomes an unknown keyword and the interval silently becomes 5 seconds (the "default is seconds" rule applies and
  the deck's intent was years). The unknown keyword is reported (`unsupported`), so strict catches it, but a non-strict
  import writes a 5 s step. I could not confirm that FISPACT-II accepts single letters; if it does this is a defect.
- Confidence: low (depends on FISPACT-II).
- Fix: if a token after `TIME n` is a one-letter word in {s,m,h,d,y}, treat it as a unit; otherwise warn that the unit was
  unrecognised rather than falling through to seconds.

### F14. robustness (low). `POWER` detail and argument handling

- Where: `apply_power` L1485-1494.
- Description. `POWER 1.0` (no `n mt` list) gives `mts == []`, so the entry claims the deck "normalizes on MT list ''".
  Words after the list (including unknown keywords, F2) are appended to the MT list in the message.
- Fix: parse `n` and take exactly `n` MT numbers; flag the rest.

---------------------------------------------------------------------------------------------------------------------

## Checked and found correct

Lexer and numbers
- `<< >>` comments: inline, multi-line, adjacent to tokens (`FLUX<<c>>1`), empty (`<<>>`), nested open inside a comment
  (the second `<<` is just text), unterminated comment fatal only if `END` was not reached (L116, L2683-2687). `*` text
  lines are recognised only outside comments and `<<` is literal inside them. No char-boundary panic: `find` returns ASCII
  offsets and `rest[*position..]` is taken at char boundaries.
- `parse_number`: `1.0E+12`, `1.E3`, `0.`, `.5`, `2D5`, `1.0d3`, `1.0+10`, `1.5-3`, signed values all parse; `inf`, `nan`,
  `infinity`, `1e400` are rejected as non-finite; the split search cannot slice inside a multibyte character.
  No false positive on nuclide names (`Co-60`, `Fe56`, `D`, `E5`).
- Keyword resolution: exact match first, so `END`/`ENDPULSE`, `PATH`/`PATHRESET`, `ATOM`->`ATOMS` (ATWO is not a prefix),
  `FLUX`/`FLUXES`->`FLUX`, `TIME`/`TIMES`, `ENDP`->`ENDPULSE`, `PULSE`/`PULS` (does not hit ENDPULSE), `READ` (ambiguous, left
  unknown). Nuclide names of the form `Xx99` cannot collide with any keyword's first four characters (`NOT1-4`, `TAB1-4`
  would need three-letter symbols). Unit tokens (`MINS`, `SECS`, ...) do not collide with `MIND`, `STEP`, `HAZA`, `HALF`.
- All keyword names in the 141 scanned decks that start a line, other than element symbols inside `MASS` lists, resolve,
  except `TOL` and `NOHEAD` (F1). All 132 FNS decks use only `TOLER`.

Interpretation
- `TIME` is an interval, not a cumulative time; units: SECS 1, MINS 60, HOURS 3600, DAYS 86400, YEARS 365.25 d (matches
  `3.1558E+07` in `test31.out`); no unit means seconds (the tutorial deck says so); the `dt` string uses `{:?}` of an
  `f64` (exponent form for 1e-6) and `parse_duration` parses it back (traced for `1e-6 s`, `1e21 y`).
- `ATOMS` in the initial phase is `noted`, in the inventory phase it triggers the pending interval; flux and partition are
  read at the trigger (documented); a trigger with nothing pending is `noted`.
- `MASS kg n` and `ELEMENT percent`: kg to g, wt_percent, element-vs-nuclide discrimination (`Fe56` in `MASS` rejected with
  "use FUEL"), case-insensitive symbols (`fe`, `FE`), repeated elements summed, short lists error rather than silently
  consume keywords. `FUEL`: nuclide names including `m`, `M`, `n` (`Te129n` -> `Te129m2`), duplicates summed, element names
  rejected, mass from mass numbers, zero total rejected. Counts bounded (1..10000), no negative-to-usize cast reachable.
- `FLUX` first positive value becomes `spectrum.total` and later steps are multipliers; `FLUX 0.` and `ZERO` make cooling
  steps; `WALL` uses the file's wall loading as reference; `POWER` persists until `FLUX`/`WALL`/`ZERO`/`POWER 0`.
- `PULSE`/`ENDPULSE`: nesting multiplies, unclosed and stray `ENDPULSE` are clean errors, `END` inside a pulse is a clean
  error, first iteration only is reported, step cap 100000 enforced whenever a step is added.
- `GETXS`: mid-run use is `unsupported` with the step to edit; group counts checked against the spectrum; `GETDECAY`
  likewise. `PROJECTILE` code 1-7 and an invalid code is an error. `NOSTABLE`, `DOSE 2` and `SORTDOMINANT` flag
  "applied from this point". `ACROSS` reaction selection (`reaction_mt`) accepts only unambiguous (n,g), (n,2n), (n,3n),
  (n,p), (n,a) and rejects isomer daughters and charged-particle projectiles. `ADCROSS`/`ADLAM` are `unsupported`.
- Panics: no `unwrap`/`expect`/unchecked index on deck input found in the non-test code. Slices `[1..]` are guarded by the
  `Some` that precedes them; `cmd.args.len() - 1` is guarded; integer conversions (`as usize`, `as u64`, `as u8`) follow
  range checks.
- `--strict`: the report is written first, the spec only if no entry is `unsupported`; exit code 1 (usage errors exit 2);
  unknown keywords count as unsupported (modulo F1/F2/F3/F6); the report records `strict` and the deck SHA-256.
- Sidecar completeness: every parsed keyword other than `ENDPULSE`, `PULSE`-repeat iterations and the F6 case has an entry
  with line, phase, status, arguments (truncated at 80 characters); sub-keywords of `OVER` have their own entries;
  `required_additions` is filled for `POWER`.
- Fluxes: group count inferred from the file (values + wall loading + title), strict layout parsing in
  `parse_fispact_fluxes`, `--files` default names and `fluxes` key lookup case-insensitive, last entry wins.
