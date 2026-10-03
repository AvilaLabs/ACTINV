# ACTINV P104 — nominal waste classification successor

Opened 2026-10-03 at the owner's existing direction to prioritize and complete
the waste extension. P103 closes FAIL after its recorded repair round: its 126
source-derived classification vectors passed, but two supplemental control
premises failed. Do not rewrite that verdict or its frozen evidence.

## Frozen scope and inherited inputs

The complete nominal single-component classification and verified class-budget
product contract, physical conversions, selector precedence, strict/inclusive
boundaries, geometry/H-3 semantics and linear interval method are inherited
unchanged from `protocols/ACTINV-P103_PROTOCOL.md`, SHA-256
`22bbaada0e757b0c1ffd5a914ca4835c12ebc6af2c61eb971c48f6e36150983e`.
No production Rust change is required by the two control-premise corrections.
The source pack/mirror SHA is
`890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7`;
the official XML and source review remain bound by `results/g0_p103_seals.json`.
The 126 public-rule vectors remain byte-identical to P103 G0b revision 3,
`bfefb655b2df52da7ccb7a93cfd7c22bdc18762917e900e828ebd97d58b2bb42`.
These are public rule controls, with previously observed diagnostic outcomes;
they are not a new blinded predictive-validation partition.

Two control premises are corrected explicitly:

- Relocating a frozen H-3 inventory from activation into declared external H-3
  preserves the final inventory/class/rows. External activity equals the relocated
  inventory, while calculated-only class is A for the empty activation inventory.
  Native vectors with no external contribution still require zero external H-3.
- Unequal timestamps are refused among selected cells of the same component.
  The refusal plant must place both cells in one component; cells in separate
  components are not that gate's premise.

Uncertainty/composition-range classification, draft fusion intrusion screening,
full BTP mixed-package qualification, scaling, jurisdictions, workbench/twin
integration and disposal acceptance remain outside this phase.

## Gates and minimum cost

G0: independently rerun the inherited source checker with its exact original
evidence comparison under P103 Amendment A, register this protocol hash, and
freeze the new control-code hashes before successor production CLI evidence.
The prior source seal is retained rather than replaced.

G1: execute all 126 unchanged public-rule vectors, including every row/column,
strictness, contributor, margin, class, coverage, inventory and geometry field.
Use the inventory-preserving unequal-cell-mass transformation and corrected
external-H-3 relocation. Exercise declared schema, geometry, membership, identity,
target, timestamp, footer and H-3 rejection gates. Plant class/sum/limit corruption
through the same independent verifier and prove byte-identical repeated output
using a distinct output path.

G2: the exact P103 tiny artificial activation fixture: Fe balance, Nb impurity,
inert stable Fe isotopes and Nb93, Nb93(n,gamma)Nb94 at 1 barn in one group,
flux 1e12 n/cm2/s, 1e6 s irradiation plus 1e6 s cooling; artificial Nb94 half-life
1e11 s, all other states stable. Targets 1/2, mass 1 g, displaced volume
1/7.8 cm3, activated metal, explicitly not-applicable external H-3, Nb spec
0.01 wt%, target C. Generate nuclear inputs only under disk-backed target.
Independently derive activities with the capture/decay closed form and check
all emitted basis/verification inventories, target times, table constraints,
intervals, binding/contributor metadata, linked usable values and joint margin.
Agreement is 1e-6 relative or 1e-12 absolute near zero. Perturb above the binding
edge within composition bounds and independently classify the full new solve.
Reject altered inventory, sum and limit through the independent verifier;
refuse nonlinear or incomplete-coverage budgets. The four named Rust interval
regressions exercise the independently specified lower-bound, constant-infeasible,
no-response/composition-cap and contributor-discontinuity singleton cases.

G3: real tiny activation component through `actinv waste` at both target times,
and budget through `actinv waste budget`; each repeats to a distinct path with
byte-identical output. Required workspace fmt/check/clippy/test and CLI test
compilation pass. Handbook build/link/browser checks pass. CI uses the data-free
successor checker after a fresh release build; every pushed-commit workflow must
be green before the checkpoint is complete.

Checker-derived P104-PASS requires every gate. Preserve every failed attempt.
One repair round is available under the standing rules; a subsequent failed gate
closes FAIL and requires another successor, never a silent retry. No acceptance
threshold or population can be changed using a measured outcome.

Coordinator executes one job at a time in the enforced 6G memory/zero-swap,
128-task/200% CPU systemd scope with disk TMPDIR. Child commands have bounded
waits and terminate/kill/reap behavior. The fixture settles the gate without a
bulk data fetch or a library-release build. Close with a session, append-only
ledger, indexed manifest, owner-authored commit, push and green Actions.
