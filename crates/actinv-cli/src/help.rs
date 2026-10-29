//! The help a person reads: the landing grouped by task, one page per command, and the near-match
//! suggestion for a mistyped command. Every page has the same parts: what the command answers, its
//! usage, an example, where its output goes, and its exit codes.

/// The command list, grouped by what a person wants to do.
pub const LANDING: &str = "\
ACTINV — activation and inventory

Start here
  data fetch               install the standard nuclear data (about 186 MiB)
  new problem.json         write a problem to edit (iron, or a library material)
  run problem.json [OUT]   solve it; the result goes to OUT or stdout
  report result.json       read a result as tables or a report page

Ask a design question
  impurities   which impurities matter, and how much of each is allowed
  budget       element limits that keep the clearance index under 1
  optimize     search a composition range for the best material
  decide       certify a design against its constraints
  study        build and run a set of cases

Waste
  waste        classify a component, then budget, bounds, package, uncertainty

Move between codes
  import-fispact  import-flux  export-openmc  export-mcnp  export-source
  export-openmc-mesh  export-r2s  export-r2s-joint

Check and inspect
  validate  doctor  inspect  data list|verify|manifest  clearance

Build nuclear data
  build-library  build-damage  build-shielding  build-covariance

Advanced
  mesh  reverse  reverse-qualified  assimilate  twin  surrogate  eval-spread

'actinv help COMMAND' for details and an example; 'actinv help exit-codes'.";

const EXIT_CODES: &str = "\
Exit codes

  0  done
  1  the work failed: a solve, a file check, a download, a write
  2  bad usage, or an input that cannot be read or is invalid
  3  a check ran and failed: verification in budget, waste budget, waste budget
     uncertainty or impurities; the output is still written

Messages go to stderr. Data go to stdout only when no output file is given.";

/// Every command name the dispatcher accepts, for the near-match suggestion.
pub const COMMANDS: &[&str] = &[
    "run",
    "validate",
    "new",
    "doctor",
    "data",
    "report",
    "inspect",
    "impurities",
    "budget",
    "optimize",
    "decide",
    "study",
    "waste",
    "import-fispact",
    "import-flux",
    "export-openmc",
    "export-openmc-mesh",
    "export-mcnp",
    "export-r2s",
    "export-r2s-joint",
    "export-source",
    "clearance",
    "build-library",
    "build-damage",
    "build-shielding",
    "build-covariance",
    "mesh",
    "reverse",
    "reverse-qualified",
    "assimilate",
    "twin",
    "surrogate",
    "eval-spread",
    "worker",
    "help",
];

/// The help page of one command, or `None` for a name that is not a command.
pub fn page(command: &str) -> Option<String> {
    let text = match command {
        "help" => "actinv help: show the command list or one command's page\n\n\
usage: actinv help [COMMAND]\n       actinv COMMAND --help\n\n  actinv help run\n  actinv help exit-codes",
        "exit-codes" => EXIT_CODES,
        "run" => "actinv run: solve a problem\n\n\
usage: actinv run PROBLEM.json [OUT.json]\n\n\
Solves the irradiation and cooling history in PROBLEM.json. The result holds\n\
inventory, activity and decay heat at every step, plus the optional outputs\n\
the problem requests: photons and dose, pathways, tables, uncertainty.\n\n\
  actinv run problem.json result.json\n\
  actinv run problem.json | jq '.steps[-1].heat_W_per_g.total'\n\n\
Writes the result to OUT.json, or to stdout without it or for `-`; progress\n\
goes to stderr. PROBLEM.json may be `-` to read stdin. Relative paths in the\n\
problem are read from the current directory.\n\
The first run with a data release prepares a private cache of a few hundred\n\
MiB under ~/.cache/actinv, or $ACTINV_CACHE_DIR.\n\n\
Exit: 0 done, 1 the run failed, 2 bad usage or an invalid problem.\n\
Next: actinv report result.json",
        "validate" => "actinv validate: check a problem before running it\n\n\
usage: actinv validate PROBLEM.json [--schema|--files|--hashes]\n\n\
  --schema   the problem's structure and values only (the default)\n\
  --files    also every input file and library index it names\n\
  --hashes   also the declared SHA-256 of each file that carries one\n\n\
  actinv validate problem.json --files\n\n\
Prints one line to stdout: the level checked, the group and step counts.\n\
A `catalog:` reference must resolve to installed data even for --schema.\n\
Whether the evaluated data suit the problem is checked by the solve itself.\n\n\
Exit: 0 valid, 1 a file check failed, 2 bad usage or an invalid problem.",
        "new" => "actinv new: write a problem to start from\n\n\
usage: actinv new OUT.json [--data-dir DIR]\n       actinv new OUT.json --material ID --source SHAPE --flux F [--irradiate DURATION] [--data-dir DIR]\n       actinv new --list\n\n\
Without options, writes iron irradiated for 5 minutes at the FNS 14 MeV neutron\n\
source, then cooled over 20 steps: the measured benchmark ACTINV is checked\n\
against. With --material, --source and --flux (all three), writes 1 g of a\n\
library material under a standard spectrum shape at total flux F (neutrons per\n\
cm^2 per s), irradiated for DURATION (default 1 y), then cooled to 1 h, 1 d,\n\
30 d, 1 y and 10 y after shutdown. It never overwrites a file. Data references\n\
are portable `catalog:` ids, read from ./actinv-data or $ACTINV_DATA_DIR;\n\
--data-dir writes absolute paths.\n\n\
  --list   print the library materials (id, name, basis) and the source shapes\n\
           (id, label), one per line, tab-separated\n\n\
The shapes are generic, not any facility's spectrum, on the 709-group structure:\n\
  fusion-14mev   a D-T line, all flux in the group holding 14.1 MeV\n\
  fission        a Watt spectrum (U-235 thermal fission)\n\
  thermal        a Maxwellian at kT = 0.0253 eV\n\
  one-over-e     flux per unit energy proportional to 1/E, 0.5 eV to 1 MeV\n\
A library composition is a starting point, not a material certificate: its\n\
note names the trace elements it leaves out, and specification maxima\n\
overstate trace elements.\n\n\
  actinv new problem.json\n\
  actinv new problem.json --material ss316 --source fission --flux 1e13\n\n\
Exit: 0 written, 2 bad usage or the file exists.\n\
Next: actinv run problem.json result.json",
        "doctor" => "actinv doctor: check that ACTINV can run here\n\n\
usage: actinv doctor [PROBLEM.json]\n\n\
Reports the version, the data directory and where it came from, the catalog\n\
version, the cache directory, and whether the files the problem needs are\n\
readable. Without a problem it checks the standard data that `actinv new`\n\
uses.\n\n\
  actinv doctor\n\
  actinv doctor problem.json\n\n\
Exit: 0 ready, 1 a file is missing or unreadable, 2 an invalid problem.",
        "data" => "actinv data: install and check nuclear data\n\n\
usage: actinv data list\n       actinv data fetch [BUNDLE] [--output DIR] [--force]\n       actinv data verify [BUNDLE] [--output DIR]\n       actinv data manifest\n\n\
  list       the bundles in this release's data catalog, with sizes\n\
  fetch      download, verify and install a bundle (default: the standard\n\
             neutron data) into DIR/v<catalog>/; DIR defaults to ./actinv-data\n\
  verify     check an installed bundle's sizes and SHA-256\n\
  manifest   print the embedded catalog as JSON\n\n\
  actinv data fetch\n\
  actinv data verify\n\n\
fetch and verify print a JSON summary with the installed paths to stdout;\n\
progress goes to stderr. --force replaces files that fail verification.\n\n\
Exit: 0 done, 1 a download or check failed, 2 bad usage.",
        "report" => return Some(format!("{}\n{}", crate::report::USAGE, crate::report::HELP)),
        "inspect" => return Some(format!("{}\n{}", crate::inspect::USAGE, crate::inspect::HELP)),
        "impurities" => "actinv impurities: screen a material's impurities\n\n\
usage: actinv impurities SPEC.json [OUT.json] [--no-verify]\n\n\
Which impurities matter, at which cooling times, for which concern, and how\n\
much of each is allowed. From one solve per element it ranks the impurities\n\
behind activity, heat, nuclide activity, contact-dose proxy, clearance index\n\
and Part 61 class; gives the worst case over the declared ranges; and gives\n\
each impurity's limit. Every reported composition is re-solved and compared\n\
unless --no-verify is given. Schema: actinv-impurities-1.\n\n\
  actinv impurities examples/impurities_ra_steel/impurities.json out.json\n\n\
Writes OUT.json, or stdout without it; the summary line goes to stderr.\n\
Handbook: docs/guide/impurities.md.\n\n\
Exit: 0 done, 1 the work failed, 2 bad usage or input, 3 verification failed\n\
(the result is still written).",
        "budget" => "actinv budget: impurity limits for the clearance index\n\n\
usage: actinv budget BUDGET.json [OUT.json] [--no-verify]\n\n\
The largest amount of each impurity that keeps the IAEA clearance index\n\
within its limit, from one coupled solve per element. Each limit is\n\
re-solved and compared unless --no-verify is given. Schema: actinv-budget-1\n\
(docs/BUDGET.md).\n\n\
  actinv budget budget.json limits.json\n\n\
Writes OUT.json, or stdout without it; the summary line goes to stderr.\n\n\
Exit: 0 done, 1 the work failed, 2 bad usage or input, 3 verification failed.",
        "optimize" => "actinv optimize: search a composition range for the best material\n\n\
usage: actinv optimize OPTSPEC.json [OUTDIR] [--resume]\n\n\
A bounded, seeded search over an actinv-optimize-1 document. Every candidate\n\
is solved by the same path as `actinv run` and appended to\n\
OUTDIR/optimize_ledger.jsonl; the ranking lands in OUTDIR/optimize_result.json.\n\
OUTDIR defaults to optimize_out beside OPTSPEC. --resume skips candidates\n\
already in the ledger.\n\n\
  actinv optimize examples/optimize_ra_steel/opt.json\n\n\
Exit: 0 done, 1 the search failed, 2 bad usage or input.",
        "decide" => "actinv decide: certify a design against its constraints\n\n\
usage: actinv decide DECISION.json [OUT.json]\n\n\
Solves the run an actinv-decide-1 document names and reports, constraint by\n\
constraint, whether the design is certified and which constraints bind.\n\n\
  actinv decide decision.json verdict.json\n\n\
Writes OUT.json, or stdout without it; the verdict line goes to stderr.\n\
A design that is not certified still exits 0: read the verdict.\n\n\
Exit: 0 done, 1 the work failed, 2 bad usage or input.",
        "study" => "actinv study: build and run a set of cases\n\n\
usage: actinv study validate STUDY.json\n       actinv study build STUDY.json [OUTDIR] [--revocations FILE]\n       actinv study run STUDY.json [OUTDIR] [--revocations FILE]\n\n\
  validate   check an actinv-study-1 document\n\
  build      expand it into problems and a manifest\n\
  run        build, solve every case and write study_record.json\n\n\
  actinv study run examples/study_fe_smoke.json\n\n\
OUTDIR defaults to STUDY.study-out. Schema: docs/STUDY.md.\n\n\
Exit: 0 done (a case that fails is recorded in study_record.json, whose\n\
verdict is then incomplete), 1 the study could not run, 2 bad usage or an\n\
invalid study.",
        "waste" => "actinv waste: classify radioactive waste\n\n\
usage: actinv waste WASTE.json [OUT.json]\n       actinv waste budget BUDGET.json [OUT.json]\n       actinv waste budget uncertainty SPEC.json [OUT.json]\n       actinv waste bounds BOUNDS.json [OUT.json]\n       actinv waste composition COMPOSITION.json [OUT.json]\n       actinv waste composition solve SPEC.json [OUT.json]\n       actinv waste intrusion-screen SPEC.json [OUT.json]\n       actinv waste uncertainty SPEC.json [OUT.json]\n       actinv waste package SPEC.json [OUT.json]\n\n\
  (none)              the US Part 61 class of a component from run results\n\
  budget              impurity limits that keep a class\n\
  budget uncertainty  those limits under nuclear-data uncertainty\n\
  bounds              the class of a declared activity range\n\
  composition         the class over declared composition ranges\n\
  composition solve   the same, from native solves of the material\n\
  intrusion-screen    opt-in screen against the 2026 draft intrusion table\n\
  uncertainty         class probabilities under nuclear-data uncertainty\n\
  package             the class of one container of items (NRC averaging BTP)\n\n\
  actinv waste component.json class.json\n\n\
Writes OUT.json, or stdout without it. Paths inside a spec are read from the\n\
spec's directory. The class is the nominal U.S. classification, not disposal\n\
acceptance; the intrusion screen is a review aid. Handbook: docs/guide/waste.md.\n\n\
Exit: 0 done, 1 the evaluation failed, 2 bad usage. `budget` and `budget\n\
uncertainty` exit 3 when a verification point misses; the result is still\n\
written.",
        "import-fispact" => {
            return Some(format!(
                "actinv import-fispact: convert a FISPACT-II deck into a problem\n\n{}\n\n\
Writes OUT.json and OUT.import.json, the report of every keyword with its line\n\
and status: mapped, approximated, noted or unsupported. Group fluxes come from\n\
--fluxes, else the `fluxes` entry of the files file (--files, else DECK_files or\n\
files beside the deck). --strict fails on any unsupported keyword.\n\n\
  actinv import-fispact deck.i problem.json --fluxes fluxes\n\n\
Handbook: docs/guide/fispact-migration.md.\n\n\
Exit: 0 converted, 1 the conversion failed, 2 bad usage.",
                crate::fispact_import::USAGE
            ))
        }
        "import-flux" => "actinv import-flux: read a transport code's flux tally\n\n\
usage: actinv import-flux openmc SOURCE.h5 OUT.ndjson --tally ID --source-rate RATE [--energy-floor-eV EV] [--window-rows N]\n       actinv import-flux meshtal SOURCE OUT.ndjson --tally ID --source-rate RATE [--energy-floor-eV EV]\n       actinv import-flux mctal SOURCE OUT.ndjson --tally ID --source-rate RATE [--energy-floor-eV EV]\n       actinv import-flux fispact FLUXES OUT.ndjson --groups GROUPS.json\n\n\
Converts an OpenMC statepoint, an MCNP meshtal or mctal file, or a FISPACT-II\n\
fluxes file into actinv-flux-1 NDJSON, one record per cell. For a transport\n\
tally, the source rate (particles/s) makes the flux absolute.\n\n\
  actinv import-flux openmc statepoint.h5 flux.ndjson --tally 1 --source-rate 1e18\n\n\
Writes OUT.ndjson; a JSON summary goes to stdout.\n\n\
Exit: 0 done, 1 the import failed, 2 bad usage.",
        "export-openmc" => "actinv export-openmc: a decay photon source for OpenMC\n\n\
usage: actinv export-openmc RESULT.json STEP OUT.py\n\n\
Writes the decay photon source of one result step (one-based) as a Python\n\
fragment for an OpenMC model. The run must request photons.\n\n\
  actinv export-openmc result.json 5 source.py\n\n\
Exit: 0 written, 1 the export failed, 2 bad usage or no such step.",
        "export-mcnp" => "actinv export-mcnp: a decay photon source for MCNP\n\n\
usage: actinv export-mcnp RESULT.json STEP OUT.sdef\n\n\
Writes the decay photon source of one result step (one-based) as an MCNP SDEF\n\
card. The run must request photons.\n\n\
  actinv export-mcnp result.json 5 source.sdef\n\n\
Exit: 0 written, 1 the export failed, 2 bad usage or no such step.",
        "export-openmc-mesh" => "actinv export-openmc-mesh: a spatial photon source for OpenMC\n\n\
usage: actinv export-openmc-mesh MESH_RESULT.ndjson STEP OUT.py\n\n\
Writes the decay photon source of every cell of an `actinv mesh` result at\n\
one step as a Python fragment for OpenMC.\n\n\
  actinv export-openmc-mesh mesh.ndjson 3 source.py\n\n\
Exit: 0 written, 1 the export failed, 2 bad usage or no such step.",
        "export-r2s" => "actinv export-r2s: a banded shutdown-dose source\n\n\
usage: actinv export-r2s MESH_RESULT.ndjson STEP OUT.ndjson\n\n\
Writes the per-cell decay photon source of an `actinv mesh` result at one step,\n\
with uncertainty bands, as actinv-r2s-source-1 for `export-source`.\n\n\
  actinv export-r2s mesh.ndjson 3 r2s.ndjson\n\n\
Exit: 0 written, 1 the export failed, 2 bad usage or no such step.",
        "export-r2s-joint" => "actinv export-r2s-joint: a shutdown-dose source with joint bands\n\n\
usage: actinv export-r2s-joint MESH_RESULT.ndjson MESH_SPEC.json STEP OUT.ndjson\n\n\
As export-r2s, with the cell-to-cell correlation of the nuclear-data\n\
uncertainty carried jointly (actinv-r2s-joint-1). MESH_SPEC.json is the\n\
actinv-mesh-spec-1 document the mesh was run from.\n\n\
  actinv export-r2s-joint mesh.ndjson mesh-spec.json 3 r2s.ndjson\n\n\
Exit: 0 written, 1 the export failed, 2 bad usage or no such step.",
        "export-source" => "actinv export-source: a shutdown-dose source for a transport code\n\n\
usage: actinv export-source {openmc|mcnp|serpent} R2S_SOURCE.ndjson OUT\n       actinv export-source alara R2S_SOURCE.ndjson OUT_DIR --shutdown-t-s T\n\n\
Converts an export-r2s source into the input of OpenMC (a <sources> XML\n\
fragment), MCNP, Serpent or ALARA. The ALARA form writes one file per cell\n\
into an empty OUT_DIR.\n\n\
  actinv export-source openmc r2s.ndjson source.xml\n\n\
Exit: 0 written, 1 the export failed, 2 bad usage.",
        "clearance" => "actinv clearance: clearance against declared limits\n\n\
usage: actinv clearance INPUT STEP [--limits PATH] [--confidence T] OUT.ndjson\n\n\
Certifies P(clearance index < 1) at one step (one-based), per cell of an\n\
`actinv mesh` result or for one run result, at confidence T (default 0.95),\n\
against the IAEA limits or an actinv-clearance-limits-1 file.\n\n\
  actinv clearance result.json 5 clearance.ndjson\n\n\
Exit: 0 written, 1 the evaluation failed, 2 bad usage.",
        "build-library" => "actinv build-library: build an activation library from evaluations\n\n\
usage: actinv build-library INPUT OUTPUT.npz [--format auto|tendl|eaf] [--projectile P] [--groups NAME|PATH] [--temperature-K K] [--workers N] [--cache DIR] [--grid-density D] [--strict-states true|false] [--profile none|endfb8|tendl] [--decay PATH] [--decay-fallback PATH] [--continue-on-error true|false] [--spallation TABLE.json] [--splice-energy-MeV MEV]\n\n\
Processes ENDF evaluations into a group-wise library and its index. Groups\n\
default to fispact-709 for neutrons and fispact-162 otherwise.\n\n\
  actinv build-library tendl/ library.npz --workers 2\n\n\
Writes OUTPUT.npz and OUTPUT_index.json; a summary line goes to stdout.\n\n\
Exit: 0 built, 1 the build failed, 2 bad usage.",
        "build-damage" => "actinv build-damage: build a displacement-damage table\n\n\
usage: actinv build-damage EVALUATION_DIR OUT.json [--projectile P] [--groups NAME|PATH] [--temperature-K K] [--cache DIR] [--method default|kinematic] [--displacement-energy EL=eV ...] [--capture-photons evaluated|one-photon|per-photon] [--continuum-recoil kinematic|evaluated] [--grid-density X] [--continue-on-error true|false]\n\n\
Group-wise damage energy and NRT/arc-dpa cross sections. The default method\n\
reads MT=444; --method kinematic derives them from the reaction kinematics,\n\
and only it takes the options after --method.\n\n\
  actinv build-damage tendl/ damage.json --method kinematic\n\n\
Exit: 0 built, 1 the build failed, 2 bad usage.",
        "build-shielding" => "actinv build-shielding: build a self-shielding table\n\n\
usage: actinv build-shielding EVALUATION_DIR OUT.json [--projectile auto|neutron] [--groups fispact-709|PATH] [--cache DIR]\n\n\
Unresolved-resonance self-shielding factors for the problem's\n\
`self_shielding` section.\n\n\
  actinv build-shielding tendl/ shielding.json\n\n\
Exit: 0 built, 1 the build failed, 2 bad usage.",
        "build-covariance" => "actinv build-covariance: build a covariance sidecar\n\n\
usage: actinv build-covariance INPUT ACTIVATION.npz OUTPUT.cov.npz [--workers N] [--cache DIR]\n\n\
Processes MF=33 covariances into a sidecar for an activation library, used\n\
by the problem's `uncertainty` section.\n\n\
  actinv build-covariance tendl/ library.npz library.cov.npz\n\n\
Exit: 0 built, 1 the build failed, 2 bad usage.",
        "mesh" => "actinv mesh: solve every cell of a flux mesh\n\n\
usage: actinv mesh MESH_SPEC.json OUT.ndjson\n\n\
Solves an actinv-mesh-spec-1 document cell by cell, from an imported flux\n\
(`import-flux`), into actinv-mesh-result-1 NDJSON.\n\n\
  actinv mesh examples/mesh_demo.json mesh.ndjson\n\n\
Writes OUT.ndjson; a JSON summary goes to stdout.\n\n\
Exit: 0 done, 1 the solve failed, 2 bad usage or input.",
        "reverse" => "actinv reverse: estimate the flux from measured activities\n\n\
usage: actinv reverse PROBLEM.json MEASUREMENTS.json [OUT.json] [--segments]\n\n\
Fits the flux normalisation, or one factor per irradiation segment with\n\
--segments, to measured activities (actinv-reverse-input-1).\n\n\
  actinv reverse examples/reverse_demo_problem.json examples/reverse_demo_measurements.json\n\n\
Writes OUT.json, or stdout without it.\n\n\
Exit: 0 done, 1 the fit failed, 2 bad usage or input.",
        "reverse-qualified" => "actinv reverse-qualified: the flux estimate with qualification\n\n\
usage: actinv reverse-qualified PROBLEM.json MEASUREMENTS.json OUT.ndjson\n\n\
As reverse, per segment, with the forward runs and checks that qualify the\n\
estimate recorded in OUT.ndjson.\n\n\
Exit: 0 done, 1 the fit failed, 2 bad usage or input.",
        "assimilate" => "actinv assimilate: fold a measurement into a result's bands\n\n\
usage: actinv assimilate --result RUN.json --assay ASSAY.json [--out OUT.json] [--emit-result UPDATED.json]\n\n\
Updates certified response bands with an assay (actinv-assay-1): a scalar\n\
value, several nuclide lines at one time, or a linear mixture such as a\n\
dose rate. Reports the gain and a consistency verdict. --emit-result writes\n\
the run result with the bands replaced, for decide, clearance or another\n\
assimilate.\n\n\
  actinv assimilate --result result.json --assay assay.json\n\n\
Exit: 0 done, 1 the update failed, 2 bad usage.",
        "twin" => "actinv twin: clearance limits over a mesh\n\n\
usage: actinv twin TWINSPEC.json [OUT.json]\n\n\
Evaluates declared clearance limits against each cell's certified bands from\n\
an `actinv mesh` result (actinv-twin-1).\n\n\
Writes OUT.json, or stdout without it.\n\n\
Exit: 0 done, 1 the evaluation failed, 2 bad usage.",
        "surrogate" => "actinv surrogate: a fast fitted model of a response\n\n\
usage: actinv surrogate fit SURSPEC.json [OUTDIR]\n       actinv surrogate eval ARTIFACT.json X.json [OUT.json]\n\n\
fit solves a design over its nodes and writes a surrogate artifact; eval\n\
evaluates it at new points. Used by optimize.\n\n\
Exit: 0 done, 1 the fit or evaluation failed, 2 bad usage.",
        "eval-spread" => "actinv eval-spread: how much the decay evaluation matters\n\n\
usage: actinv eval-spread PROBLEM.json [OUT.json]\n\n\
Solves the problem with each decay evaluation as primary and reports the\n\
spread of every quantity in log space. Needs decay.fallback.\n\n\
Exit: 0 done, 1 a solve failed, 2 bad usage.",
        "worker" => "actinv worker: the desktop app's solver process\n\n\
usage: actinv worker\n\n\
Reads actinv-worker-request-1 lines on stdin and answers one\n\
actinv-worker-response-1 line each on stdout. Not for direct use.",
        _ => return None,
    };
    Some(text.to_string())
}

/// The usage lines of one command's page and a pointer to the page, for a usage error.
pub fn usage(command: &str) -> String {
    let Some(text) = page(command) else {
        return unknown(command);
    };
    let lines: Vec<&str> = text
        .lines()
        .skip_while(|line| !line.starts_with("usage:"))
        .take_while(|line| line.starts_with("usage:") || line.starts_with("       actinv"))
        .collect();
    format!("{}\nSee 'actinv help {command}'.", lines.join("\n"))
}

/// The closest command name to a mistyped one, if it is close enough to be a typo.
pub fn suggest(typed: &str) -> Option<&'static str> {
    COMMANDS
        .iter()
        .map(|name| (edit_distance(typed, name), *name))
        .filter(|(distance, name)| *distance <= (name.len() / 3).max(1))
        .min()
        .map(|(_, name)| name)
}

/// The message for a word that is not a command.
pub fn unknown(typed: &str) -> String {
    match suggest(typed) {
        Some(name) => {
            format!("actinv: unknown command '{typed}'. Did you mean '{name}'? See 'actinv help'.")
        }
        None => format!("actinv: unknown command '{typed}'. See 'actinv help'."),
    }
}

/// Edits between two words, counting a swap of neighbouring letters as one ("rnu" is one from "run").
fn edit_distance(a: &str, b: &str) -> usize {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let mut d = vec![vec![0usize; b.len() + 1]; a.len() + 1];
    for (i, row) in d.iter_mut().enumerate() {
        row[0] = i;
    }
    for (j, cell) in d[0].iter_mut().enumerate() {
        *cell = j;
    }
    for i in 1..=a.len() {
        for j in 1..=b.len() {
            let cost = usize::from(a[i - 1] != b[j - 1]);
            d[i][j] = (d[i - 1][j - 1] + cost)
                .min(d[i - 1][j] + 1)
                .min(d[i][j - 1] + 1);
            if i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1] {
                d[i][j] = d[i][j].min(d[i - 2][j - 2] + 1);
            }
        }
    }
    d[a.len()][b.len()]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_command_has_a_page_with_usage_and_exit_codes() {
        for name in COMMANDS {
            let text = page(name).unwrap_or_else(|| panic!("{name} has no help page"));
            assert!(text.contains("usage:"), "{name} page has no usage");
            if !matches!(*name, "worker" | "help" | "inspect" | "report") {
                assert!(text.contains("Exit:"), "{name} page has no exit codes");
            }
        }
    }

    #[test]
    fn landing_names_every_user_command() {
        for name in COMMANDS {
            if !matches!(*name, "worker" | "help") {
                assert!(LANDING.contains(name), "landing omits {name}");
            }
        }
    }

    #[test]
    fn a_typo_gets_the_nearest_command_and_noise_gets_none() {
        assert_eq!(suggest("rnu"), Some("run"));
        assert_eq!(suggest("valdate"), Some("validate"));
        assert_eq!(suggest("impurity"), Some("impurities"));
        assert_eq!(suggest("frobnicate"), None);
        assert!(unknown("rnu").contains("Did you mean 'run'?"));
        assert!(!unknown("frobnicate").contains("Did you mean"));
    }

    #[test]
    fn a_usage_error_shows_the_usage_lines_and_the_page() {
        assert_eq!(
            usage("run"),
            "usage: actinv run PROBLEM.json [OUT.json]\nSee 'actinv help run'."
        );
        assert!(usage("data").contains("       actinv data manifest\nSee"));
    }

    #[test]
    fn edit_distance_counts_single_edits() {
        assert_eq!(edit_distance("run", "run"), 0);
        assert_eq!(edit_distance("rnu", "run"), 1);
        assert_eq!(edit_distance("", "run"), 3);
        assert_eq!(edit_distance("budgte", "budget"), 1);
        assert_eq!(edit_distance("impurity", "impurities"), 3);
    }
}
