# TENDL-2025 threshold technical note

Author-review draft prepared for Connor Avila on 14 September 2026.

## Deliverables

- `TENDL_threshold_note.docx` — editable Word manuscript.
- `TENDL_threshold_note.pdf` — formatted eight-page review copy.
- `manuscript.html` — readable generated manuscript source.
- `build_paper.py` — manuscript text, result-driven tables, figure and document builder.
- `figures/threshold_sensitivity.{png,svg,pdf}` — publication figure, including vector versions.
- `TENDL_threshold_supplement.zip` — standalone analysis and reproducibility supplement.
- `supplement/` — analysis scripts, numerical results, independent checks and source identities.
- `PROTOCOL.md` and `EXECUTION_LOG.md` — frozen study definition and execution history.
- `review/` — claim mapping, remaining submission decisions and internal source snapshots.

The manuscript uses A4 pages, 25.4 mm margins, Times New Roman body text,
numbered sections, equations, tables, references and page numbers. Tables do not
split across pages. The figure is embedded in Word and PDF. Formatting is a
general technical-note format; no destination journal has been selected.

## Findings

All four original source contradictions reproduced. The independent parser and
quadrature checks passed; maximum absolute disagreement was 7.325e-15 b.
Under the fixed illustrative Gaussian spectrum, Cl-35 ground-state production
is 1.2808287858108072 b before and 0.0011452245538720223 b after the
single-ordinate intervention. At exactly 14.1 MeV all four point values are
unchanged. The study measures reaction-rate sensitivity, not an inventory,
activity, dose, or experimentally validated correction.

The old ACTINV software manuscript and installed nuclear data have not been
changed. The private correspondence is paraphrased from the repository record;
its technical cause and planned release correction have not been independently
verified against TALYS source or a newly distributed official evaluation.

## Reproduction

See `supplement/README.md`. The supplement uses the four original local files
and rejects different hashes. `compare.py` uses the Python standard library;
`check.py` additionally needs SciPy. Document generation needs Matplotlib,
LibreOffice and its Python UNO bridge. The recorded environment is in
`supplement/environment.json`.

To regenerate the documents after successful analysis and checking, run from
this directory under the same bounded systemd scope described in the supplement:

```sh
mkdir -p work
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- timeout --kill-after=10s 120s env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 TMPDIR="$PWD/work" /usr/bin/python3 build_paper.py
```

Edit manuscript prose in `build_paper.py`; the HTML, Word and PDF are generated.
Do not edit the numerical evidence to change a manuscript result.
