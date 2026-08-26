#!/bin/bash
# Fetch the small public data subset the CI controls need and verify every file against scripts/ci_data.sha256.
# Nuclear data are never stored in this repository; see docs/DATA.md for sources and terms of use.
set -euo pipefail
DEST=${ACTINV_CI_DATA:-$HOME/actinv-ci-data}
mkdir -p "$DEST/tendl" "$DEST/decay"
IAEA="https://www-nds.iaea.org/public/download-endf"
get() {  # url  destination-path
  local url=$1 out=$2
  [ -f "$out" ] || { echo "fetching $(basename "$out")"; curl -sSfL -m 900 -A "actinv-ci (https://github.com/AvilaLabs/ACTINV)" -o "$out" "$url"; }
}
get "$IAEA/ENDF-B-VIII.0/_backup-by-NSUB/zip/endf-b-viii-0_decay.sublib.zip" "$DEST/decay/endf-b-viii-0_decay.sublib.zip"
[ -f "$DEST/decay/endf-b-viii-0_decay.dat" ] || unzip -oq "$DEST/decay/endf-b-viii-0_decay.sublib.zip" -d "$DEST/decay"
while read -r name; do
  get "$IAEA/TENDL-2023/n/${name%.dat}.zip" "$DEST/tendl/${name%.dat}.zip"
  [ -f "$DEST/tendl/$name" ] || unzip -oq "$DEST/tendl/${name%.dat}.zip" -d "$DEST/tendl"
done < scripts/ci_tendl_files.txt
( cd "$DEST" && sha256sum -c --quiet "$OLDPWD/scripts/ci_data.sha256" ) && echo "data subset verified in $DEST"
