# Why the FNS iron benchmark misses the measurements

The evidence does not establish a general ACTINV solver failure. It identifies
opposite model-to-measurement biases in two experimental campaigns and a
smaller, specific Mn57 production difference against the archived FISPACT
calculation. Neither result qualifies ACTINV as experimentally accurate to 5%.
No model inputs, nuclear data or solver code were adjusted.

## Both campaigns, unchanged inputs

| Campaign | Points | Geometric mean calculated/measured | Pointwise C/E | Within reported errors |
| --- | ---: | ---: | ---: | ---: |
| 1996, five-minute Fe irradiation | 20 | 0.92628 | 0.88311–0.96227 | 5/20 |
| 2000, five-minute Fe irradiation | 21 | 1.05950 | 1.02533–1.08359 | 8/21 |

ACTINV is systematically low against 1996 and systematically high against
2000. Both are retained; the second campaign does not replace or excuse the
first result. Reported errors have no assumed confidence level or covariance.

The same mass, total flux, irradiation duration and released data are used,
but each campaign has its own archived spectrum and exact measurement times.
A controlled swap of the 2000 spectrum into the 1996 schedule changes predicted
heat by only **−0.073% to +0.837%**. Its counterfactual geometric mean C/E is
0.92968 instead of 0.92628. Spectrum differences therefore do not explain the
opposite biases. This is a sensitivity calculation, not a third experiment.

Gilbert and Sublet already report unexplained variation between five-minute
iron irradiation batches in section 4.2 of their
[2019 FISPACT study](https://scientific-publications.ukaea.uk/wp-content/uploads/GILBERT_SUBLET_NUCL_FUS_2019.PDF).
Our findings are consistent with that observation. They do not identify which
campaign, source normalization, measurement correction or nuclear datum is
responsible. Do not label either measurement campaign erroneous on this basis.

## What the independent controls rule out

A separate Python ENDF parser reconstructs each nuclide's activity from its
CLI atom count and evaluated half-life, then its heat from the three total
mean decay energies. Across both original campaigns, reconstructed heat agrees
with CLI heat within **4.45e-16 relative**. This checks energy accounting and
units; it shares evaluated decay data and does not prove those data correct.

Starting from the first measured-time inventory, the Mn56 cooling curve agrees
with exponential decay within **2.16e-10 relative**. This checks the dominant
cooling behavior. It does not independently verify irradiation production,
secondary feeding or the complete solver over other applications.

The original 1996 measurement times, cooling flux, sample mass and flux
normalization pass the existing source-integrity checks. No percent-scale
heat summation, unit conversion or dominant cooling error was found.

## The remaining ACTINV–FISPACT difference

The archived FISPACT/TENDL-2017 calculation also underpredicts the 1996
measurements: geometric mean C/E **0.93592**. Its similar bias is corroborating
evidence, not an identical-data solver comparison: current ACTINV uses the
released patched TENDL-2025 library and independently selected decay data.

At exactly 66 seconds after irradiation, the printed FISPACT inventory gives:

| Nuclide | ACTINV/FISPACT atoms | ACTINV/FISPACT heat | ACTINV/FISPACT mean heat per decay |
| --- | ---: | ---: | ---: |
| Mn56 | 0.99720 | 0.99511 | 0.99671 |
| Mn57 | 0.81794 | 0.81849 | 1.00061 |
| Fe53 | 1.00000 | 0.99228 | 0.99217 |

The largest early-time difference is fewer **Mn57 atoms**, not missing decay
energy. Its first-point heat difference is about 0.00276 microW/g. Mn56 atom
counts differ by about 0.28%; Fe53 atom counts match within the printed precision.
FISPACT output is rounded, so small inferred mean-energy differences are
approximate. Raw-output and historical-summary heat values also differ slightly
because of their printed precision; the receipt preserves both sources.

This localizes the next investigation to Mn57 production: compare the exact
Fe57 reaction-rate collapse and all feeds using identical activation and decay
data, then check the irradiation inventory against an independent analytic
or matrix solution. The current comparison cannot distinguish a library
cross-section difference from an ACTINV production-path treatment difference.
There is no justified numerical or physics correction to apply yet.

## Reproduction and evidence

Run the original benchmark first, then, in the bounded execution environment
required by the repository's AGENTS.md:

```sh
python3 controls/fns_iron.py --data-root target/fns-data
python3 controls/fns_iron_diagnose.py --data-root target/fns-data
python3 controls/test_fns_iron_diagnose.py
```

The diagnostic uses the CLI at `target/release/actinv`, independently parses
the hash-pinned released decay files, and reads bounded named members from the
hash-pinned CoNDERC archive without extraction. Three CLI cases execute
sequentially: baseline 1996, actual 2000, then the spectrum counterfactual.
Subprocesses have 180-second timeouts and terminate/reap on timeout.

[Protocol](../../protocols/FNS-IRON-DIAG-001.md),
[Amendment 1](../../protocols/FNS-IRON-DIAG-001-AMENDMENT-1.md),
[initial receipt](initial.json), [extended receipt](diagnosis.json),
[append-only ledger](ledger.md).
Raw inputs and CLI outputs are retained in the linked CI artifacts. The runner
compares fresh campaign summaries, dominant-nuclide heat and spectrum sensitivity
against the recorded receipt; this is a reproducibility gate, not an
experimental-accuracy acceptance test.
