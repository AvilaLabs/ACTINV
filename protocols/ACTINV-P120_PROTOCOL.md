# P120 — opt-in single-case neutron spectrum rebinning

Registered 2026-10-06 before feature code or new solver observations, following
the owner's instruction to implement the FARIS input improvement. Base:
`d1d14b33471f1286baa50f3ffd6615cfd11070a1`, with all six implementation/closure
workflows and all four master-push workflows observed green.

## Frozen change and boundaries

Add optional `spectrum.rebin: "equal_lethargy"` to the shared Rust specification
and Python `Spectrum(rebin=...)`. Omitted/null means the existing strict path;
omit the absent field during serialization. Enable it only for neutron custom
spectra with explicit, finite, positive, strictly increasing ascending eV
boundaries. Group values are group-integrated physical flux in n cm^-2 s^-1;
`total` normalization and `descending` ordering retain their existing meaning.
Source boundaries must lie entirely inside the activation-library range;
reject zero edges, out-of-range edges and unknown methods explicitly. No
floor/cap/extrapolation option or silent lost-flux allowance is added.

Reuse `flux::rebin_equal_lethargy`. Matching grids take its identity path.
Report method, flat flux per unit lethargy assumption, source/destination
totals, underflow/overflow and relative closure only for opted-in runs. Preserve
the declared source input identity when internally solving on library groups.
Preserve the original source-group tally-error basis using the existing P93
group-weight machinery; splitting one source group must not invent independent
destination-group errors. Require missing errors to fail when flux uncertainty
is requested. Reject schedule step spectra when base rebinning is requested;
this phase does not introduce per-step source-grid origins. Irradiation and
cooling durations and multipliers remain supported.

No new reaction solver, nuclear data, group library, photon output grid,
per-cell schedule, replacement-history model or waste qualification is added.
Document existing mesh `materials` overrides keyed by cell id (implemented in
`bb2e8e5` on 2026-09-27); correct the stale same-material claim and parking note.
Mesh schedules remain shared. Rebinning assumes a within-group shape; integral
conservation alone does not validate threshold/resonance reaction rates.

## Source and history ownership

Production changes are confined to `crates/actinv-core/src/{spec,run,mesh,flux,study}.rs`
and `python/src/objects.py`, including necessary absent-field defaults in existing
Rust test/helper literals. Inline tests, new P120 controls/fixtures/recorders,
Python object regressions, the workflow wiring, handbook specification/problems/
qualification/mesh documentation, PARKING correction, append-only phase registry,
manifest and P120 verification records are permitted. Bind the complete actual
changed-path set and hashes before publishing. Other Rust implementation files,
all existing scientific fixtures, frozen controls, results, failure archives and
P103–P119 protocols/dispositions remain byte-identical to the base. Do not edit
historical checkers to pretend current sources equal their checkpoint.

Replace only the P103–P118 source-sensitive workflow block with a separately
labelled historical replay at clean detached base commit `d1d14b3`. The pinned
base workflow SHA-256 is
`e60c5a22639d6f8890612ca713df84b0d0affda594616ef7e710fc8d779b3c9b`.
Execute its complete original Python command sequence against unchanged base
sources and a reference CLI built from that same checkout, explicitly selected
by absolute `ACTINV_BIN`, with its own disk target directory. Verify checkout
HEAD, source cleanliness, reference/candidate binary identities and actual exits.
Preserve every original command and acceptance predicate; all candidate workspace
quality/build gates before the block and remaining candidate controls after it
stay unchanged. Add candidate feature proof and the exact unchanged P116 G1
campaign using `check_p116.py --g1-only --no-write` with the candidate CLI.
This is a current scientific compatibility replay, separate from historical G0
source identity and from the still-open broader waste qualification.

## Frozen independent controls

`controls/fixtures/p120/rebin.json` is registered with this protocol before code.
Pure overlap case: source edges [1,4,16], source flux [8,16], destination edges
[1,2,8,16] -> [4,12,8], total 24. These halves follow literal powers-of-two
lethargy widths, independently of the production converter. The narrower
destination [2,8] accounts for 4 underflow, 12 destination and 8 overflow;
the single-case opt-in must refuse that lost-range input.

For CLI/Python and analytic kinetics controls reuse the unchanged P11 decay
writer and row convention; P11 fixture source SHA-256:
`6281020bd7f489513f10a3330a9205c75ebda3e868520491a887ac47f2842c24`.
Generate a tiny two-group synthetic neutron library with edges [1,4,16], rows
in the P11 order and cross sections [[0.2,0.8],[0.2,0.8],[0.1,0.4],[0.1,0.4]]
barns. A single source group [1,16] with total 1e24 maps to [5e23,5e23].
Fe56 starts at 1 atom/g; removal rate is 0.75 s^-1, Mn56 production rate 0.5
s^-1 and Mn57 production rate 0.25 s^-1 at multiplier one. P11 half-lives
are 2 and 3 s. Freeze schedule (0.7 s,1), (0.2 s,0), (0.4 s,2), (0.6 s,0).
Independent coupled recurrence is P'=P exp(-r dt), and
D'=D exp(-lambda dt)+k P [exp(-r dt)-exp(-lambda dt)]/(lambda-r).
Cooling uses r=k=0. Compare every target/product inventory, activity and heat
at every endpoint against this analytic recurrence (`rel_tol=1e-10`,
`abs_tol=1e-12`). Independently specified fine-grid input and one-cell mesh
must match the rebinned single calculation's scientific vectors to 1e-12
relative/absolute; raw equality is recorded where applicable.

Test normalized and absolute flux, descending order, identity grids, cached
reuse across changing source totals, missing option/unknown mode, malformed
boundaries, invalid flux, zero/outside range, non-neutron refusal, step-spectrum
refusal and zero-flux cooling. Flux uncertainty must retain one parameter for
the one-source-group case; compare response sensitivity to a central finite
difference of physical source flux at h=1e-4, with tolerance
1e-4*abs(FD)+1e-10*abs(response). Cover descending source errors and missing
errors. Default results against the base CLI compare all fields after removing
only known timing keys `ms`/`elapsed_ms`; no new unconditional ledger fields.

Keep the P113 fixture SHA
`e6bcebc856c7faaa571455a45c81da49e988a34bfd1f7149bc67656d93d5f956`
and exact frozen P116 G1 SHA
`74f6eab80ee223c81b6f7391f3f4147d7fa38ffeac55485f3d2a05e8618a65b9`.
Require all 35 requests/138 comparisons, mutation/refusal checks and repeated
output equality on the candidate; the fixed G1 document must remain identical.
An evaluated-data smoke calculation on existing TENDL-2025 catalog 1.1.0 may
add mapped-versus-direct evidence with exact artifact hashes; it is not a
measurement validation and cannot replace the independent synthetic controls.

## Qualification and workstation requirements

Coordinator alone runs executable jobs, serially, in enforced systemd scopes:
MemoryMax=6G, MemorySwapMax=0, TasksMax=128, CPUQuota=200%, CARGO_BUILD_JOBS=1,
RUST_TEST_THREADS=1, RAYON_NUM_THREADS=2, disk TMPDIR `target/preflight-tmp`.
Inspect actual cgroup limits before launch; no unlimited fallback. Review all
process launch paths, require bounded waits/termination/reaping and cancellation
race regressions. Never execute a Rust test's current_exe recursively. CLI children
are bounded to 120 s; scientific jobs to 1200 s; build/quality jobs to 1800 s.
Historical replay is a serial command sequence whose individual gates are each
bounded; retain its completed/failed receipts without overwriting observations.

Require protocol/fixture registration before edits; analytic rebin and kinetics,
direct/mesh/cached/uncertainty controls, Python mapping/native parity, all candidate
workspace fmt/check/clippy/tests (all targets/features), fresh release, historical
replay and candidate P116 G1 exact replay. Stage code before index manifest refresh;
compile CLI test modules before push. Record actual argv/exits/resources/logs,
software/data versions, source/binary hashes and skipped checks explicitly.
Implementation may iterate normally, with failed attempts retained and no weakened
frozen thresholds or retroactive scientific expected-value edits. A threshold
failure that needs a scientific contract change requires a separately registered
successor, not a rewritten result.

Publish only after required local gates pass; require all six implementation
workflows green on the exact candidate SHA. Observe closure/push workflows before
treating publication as complete. Use plain owner commit identity/messages.
Prior P116/P117/P118 FAIL records and broader waste qualification stay unchanged.
