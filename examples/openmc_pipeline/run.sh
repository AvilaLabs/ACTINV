#!/usr/bin/env bash
# End-to-end OpenMC -> ACTINV pipeline:
#   OpenMC transport -> import-flux -> actinv mesh -> decay-heat table
#   -> (optional) decay-photon source fragment for a downstream OpenMC run.
#
# Prereqs:
#   * `actinv` on PATH (pip install actinv) and `actinv data fetch` run once
#   * python3 with openmc >= 0.14 and numpy
#   * OPENMC_CROSS_SECTIONS pointing at an HDF5 cross_sections.xml
set -euo pipefail
cd "$(dirname "$0")"

ACTINV=${ACTINV:-actinv}
PYTHON=${PYTHON:-python3}
LIB=${ACTINV_LIBRARY:-../../actinv-data/v1.1.0/activation/tendl-2025-neutron-709g.npz}
SRC_RATE=${SOURCE_RATE:-1e15}
TALLY=${TALLY_ID:-42}

echo "==> OpenMC transport"
$PYTHON model.py --library "$LIB" --tally-id "$TALLY"
SP=$(ls -t statepoint.*.h5 | head -1)

echo "==> actinv import-flux openmc"
$ACTINV import-flux openmc "$SP" flux.ndjson \
  --tally "$TALLY" --source-rate "$SRC_RATE"

SHA=$(sha256sum flux.ndjson | cut -d' ' -f1)
cat > mesh.json <<EOF
{
  "spec": "actinv-mesh-spec-1",
  "title": "OpenMC 14 MeV point source, 1 cm Fe cube",
  "projectile": "neutron",
  "library": {"path": "catalog:tendl-2025-neutron-709g"},
  "decay": {"primary": "catalog:endfb-viii-0-decay", "fallback": "catalog:jeff-3-3-decay"},
  "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"FE": 100.0}},
  "flux": {"path": "flux.ndjson", "sha256": "$SHA"},
  "schedule": [
    {"dt": "300 s", "flux": 1.0},
    {"dt": "60 s",  "flux": 0.0},
    {"dt": "240 s", "flux": 0.0},
    {"dt": "1 h",   "flux": 0.0},
    {"dt": "23 h",  "flux": 0.0},
    {"dt": "6 d",   "flux": 0.0}
  ],
  "options": {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-8, "temperature_K": 293.6}
}
EOF

echo "==> actinv mesh"
$ACTINV mesh mesh.json result.ndjson

echo "==> decay heat"
$PYTHON summarize.py result.ndjson

echo "==> decay-photon source for a downstream OpenMC run (cooling step 2)"
$ACTINV export-openmc-mesh result.ndjson 2 photon_source.py
head -5 photon_source.py
echo "wrote photon_source.py — import it as a module inside an OpenMC photon run"
