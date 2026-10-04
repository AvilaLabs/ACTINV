#!/usr/bin/env bash
# Compatibility entry point for the verified CI seed installer.
set -euo pipefail
DEST=${ACTINV_CI_DATA:-$HOME/actinv-ci-data}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
python3 "$ROOT/scripts/fetch_ci_seed.py" --mode controls --destination "$DEST"
(cd "$DEST" && sha256sum -c --quiet "$ROOT/scripts/ci_data.sha256")
echo "verified controls seed installed in $DEST"
