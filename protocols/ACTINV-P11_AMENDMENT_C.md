# ACTINV P11 Amendment C — local CI entry-point resolution

**Date:** 2026-08-27. **Parent protocol:** `ACTINV-P11_PROTOCOL.md`
(`fb9964d523e9bad8e2175ff24b5ca0e14982d9bfdf46962664a00a10925cc2d4`).

The P11 regression run executed `controls/ci_end_to_end.py` directly from the checkout after building the release
workspace and the local PyO3 extension, but without installing that extension into the active Python environment.
The control unconditionally imported an installed `actinv` package and therefore stopped with
`ModuleNotFoundError`; the Rust CLI half of the same control had already passed. GitHub Actions installs the wheel
before this control, so the failure was specific to the documented local regression path rather than a production or
scientific result.

The control now prefers the installed package and, only when that import is absent, loads the explicitly named
`ACTINV_PYTHON_LIBRARY` or the checkout's release extension. Both paths still execute the same PyO3 module and still
require exact CLI/Python identity plus the unchanged absolute reference tolerance. The repaired local run has exact
entry-point identity and a worst reference deviation of `2.64698e-23 W g^-1`, against the frozen
`1e-17 W g^-1` criterion. No production code, physics input, tolerance or result field changed.

The successful phase verdict remains `P11-CONDITIONAL`.
