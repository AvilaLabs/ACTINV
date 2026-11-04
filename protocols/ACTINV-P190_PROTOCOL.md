# ACTINV-P190 — mixture self-shielding from OpenMC's pointwise tables, against OpenMC continuous energy

## Purpose

P178 qualified `self_shielding.mixture` with ENDF-6 evaluations: on four models, every listed resonance-capture rate
came within materiality of OpenMC's continuous-energy rate on the same ENDF/B-VIII.1 data. Each run reconstructs the
evaluations' resonances, which costs about a minute per copper region.

ACTINV can now take the mixture's cross sections as pointwise tables (`self_shielding.mixture.pointwise_files`,
`d616789`), and `actinv.openmc.export_pointwise` writes them from the HDF5 library OpenMC itself transports with. That
is what the OpenMC adapter would use (`ActinvR2SManager(self_shielding="mixture")`). It differs from P178's arm in two
ways: the cross sections are OpenMC's processed 294 K tables, not ACTINV's reconstruction at 293.6 K; and each table
covers the nuclide's whole energy range, so every group is shielded, not only the resolved range. The question:

> On P178's four models, with the tables exported from the library OpenMC's tallies used, does ACTINV's mixture
> self-shielding bring its 709-group resonance-capture rates within materiality of OpenMC's continuous-energy rates,
> without moving the rates it is not meant to move?

A pass is the evidence for making `self_shielding="mixture"` the adapter's default. A PASS can show that the
correction fails.

## Disclosure of what has been seen

- **Nothing is held out.** P178's models, OpenMC rates, unshielded ACTINV rates and its evaluation-based mixture rates
  are committed and were read in full (P178's largest capture |r_mix| was 0.033, model E). P190 reuses P178's OpenMC
  references (`results/p178/openmc_*.json`) as they are; no OpenMC transport is rerun.
- The pointwise tables of copper (Cu63, Cu65) were compared with the reconstructed plan while developing the feature
  (P178's copper case: 1.2 s instead of 71 s). No rate of P190's arm was computed for any model before registration
  except as below.
- **Rehearsal.** `controls/p190.py run --model B` ran once before registration with the then-current binary. Its
  exit status, the exported nuclide list (Cu63, Cu65, none missing), the ledger block's keys and its table
  temperatures (294 K) were read. No rate, ratio or factor was read, `assemble` and `check` were not run, and the
  outputs are deleted.

## Method

`controls/p190.py` imports P173's and P178's sealed code unchanged and writes nothing P178 reads.

- **Tables (`run --model M`).** For each model, `export_pointwise` writes one `actinv-pointwise-1` table per nuclide
  of the model's OpenMC material (atom density > 0) from `cross_sections.xml` (SHA-256 pinned by P173/P178), at the
  stored temperature within 1 K of 293.6 K, which is 294 K. Each table's SHA-256 is read back.
- **ACTINV arm.** For each tallied region, one `actinv run` with P178's mixture spec (P178's ENDF/B-VIII.1 709-group
  library, decay data, the region's OpenMC flux, the model's composition, 293.6 K), its `self_shielding` replaced by
  `{"mixture": {"pointwise_files": [...]}}`. The wall time of each run is recorded.
- **Rates.** As P178: `R_mixture = Σ_g σ_g s_g φ_g` from the ledger's applied factors;
  `r_mix = ln(R_mixture / R_openmc)`, with P178's standard error.
- **Assemble.** `results/p190_record.json`: P173's assembly of P178's committed files, P190's rows folded in where P178
  folded its mixture rows, the digests of the nine P178 files read, and the pointwise arm (tables, binary, cgroups, wall
  times).

Every step runs in the bounded scope: 6 GiB, no swap, 128 tasks, CPUQuota 200%. The binary is built in that scope
from a clean checkout of the registration commit (`cargo build --release --locked -p actinv-cli`), and all four models
use it.

## Gates

- **G0:** the SHA-256 of this protocol and of `controls/p190.py`, `controls/p173_ce_rates.py`,
  `controls/check_p173.py`, `controls/p178_ce_rates.py` and `controls/check_p178.py` are in
  `protocols/protocol_hash.txt`.
- **G1:** provenance.
  - All four models ran at the registration commit with a clean tracked tree, with one binary and one
    `cross_sections.xml`, which matches P173/P178's pin.
  - Every table comes from that library's directory at 294 K, with a digest.
  - Every run's cgroup is the bounded scope, and every region has a wall time.
  - The P178 files read hash as recorded, and every row's OpenMC and unshielded values equal P178's committed record.
- **G2:** P178's G2 fidelity checks on `r_mix`, then P178's decision rules on the pointwise arm:
  - capture: no used capture row is material in `r_mix` (|r_mix| > 0.05 and > 3 standard errors);
  - controls: no used non-capture row moves by more than 0.01 in log ratio, or becomes material when it was not;
  - at least one capture row is used (R_openmc at ≤ 0.5% relative standard error).

  The outcome must be QUALIFIED or NOTHING TO CORRECT.
- **G3:** CI green on the evidence commit.

**P190-PASS** if G0–G3 hold. One repair round (an amendment with a fresh run) is allowed. On P190-PASS the adapter's
default may become `self_shielding="mixture"`; on P190-FAIL it stays off and the pointwise mode keeps its "not yet
compared" note.

## Allowed sentences

On P190-PASS, filled by `controls/p190.py check` from the record (x: the largest capture |exp(r_mix) − 1|, rounded up
to 0.1%; t: the largest region wall time, rounded up to 0.1 s):

"On P178's four models, ACTINV's mixture self-shielding with pointwise cross sections exported from OpenMC's
ENDF/B-VIII.1 HDF5 library at 294 K, instead of reconstructed ENDF-6 evaluations, brought every listed
resonance-capture rate within {x}% of OpenMC's continuous-energy rate on the same data; each region's run took at most
{t} s."

If some listed capture rows are not used, the verdict adds: "{n} of {N} listed capture rows were not scored or did not
reach a 0.5% standard error and are not covered by the sentence above."

The sentence says nothing about experiment, other models, or the evaluation-file mode.

## Commands (coordinator)

From a worktree at the registration commit, with `ACTINV_BIN` set to the binary built there and
`OPENMC_CROSS_SECTIONS` set to the pinned `cross_sections.xml`, each inside the bounded scope:

- `python3 controls/p190.py run --model E`, then `I`, `H`, `B`;
- `python3 controls/p190.py assemble`;
- `python3 controls/p190.py check`.
