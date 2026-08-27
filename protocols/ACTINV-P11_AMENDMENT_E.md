# ACTINV P11 Amendment E — CI dependency-audit coverage

**Date:** 2026-08-27. **Parent protocol:** `ACTINV-P11_PROTOCOL.md`
(`fb9964d523e9bad8e2175ff24b5ca0e14982d9bfdf46962664a00a10925cc2d4`).

The final regression review found that `controls/check_dependencies.py` maintains an explicit list of Python entry
points executed by CI. The workflow had gained the P11 G3--G5 controls, but that audit list had not gained the same
three paths. The audit therefore passed without traversing the new controls and their local imports.

The entry list now includes `g3_p11_sensitivity.py`, `g4_p11_propagation.py` and
`g5_p11_entry_points.py`. The repaired audit traverses their import closure and finds no undeclared dependency;
NumPy and SciPy remain declared by `requirements-ci.txt`. No dependency, production code, physics input, tolerance or
scientific result changes.

The successful phase verdict remains `P11-CONDITIONAL`.
