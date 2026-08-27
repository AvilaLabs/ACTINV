# ACTINV P11 Amendment B — canonical mesh control footer

**Date:** 2026-08-27. **Parent protocol:** `ACTINV-P11_PROTOCOL.md`
(`fb9964d523e9bad8e2175ff24b5ca0e14982d9bfdf46962664a00a10925cc2d4`).

The first G5 execution planted no `volume_cm3` on its one canonical flux cell but nevertheless declared a
`volume_integrated_flux` footer. The existing P8 canonical contract correctly rejected that inconsistent record before
mesh solving. The repaired control omits the optional volume-integrated field, as required when cell volumes are
absent. It retains the exact group flux and summed-flux closure used by the ordinary run. No production code,
scientific datum, entry-point normalization or acceptance criterion changed.

The repaired G5 run has exact CLI/Python/prepared/mesh identity after removing only entry labels and timing, rematches
all five input hashes, and passes all ten fail-closed plants. This additional repair record leaves the successful phase
verdict `P11-CONDITIONAL`.
