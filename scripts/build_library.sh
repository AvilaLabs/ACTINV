#!/bin/bash
# Build a 709-group ACTINV activation library from a directory of ENDF-6 files.
#   scripts/build_library.sh FILES_DIR OUT_DIR NAME [DENSE] [WORKERS] [VMEM_KB]
# Resumable: each target is cached under OUT_DIR/cache_NAME and reused when the physics code is unchanged.
set -u
FILES=${1:?files dir}; OUT=${2:?out dir}; NAME=${3:?library name}; DENSE=${4:-1}; WORKERS=${5:-5}; VMEM=${6:-3000000}
PY=${ACTINV_PYTHON:-$HOME/.venvs/w003env/bin/python}
cd "$(dirname "$0")/.." || exit 1
ulimit -v "$VMEM"                       # per process; WORKERS x VMEM must fit in available memory
export PYTHONWARNINGS=ignore
"$PY" controls/tendl_build.py "$FILES" "$OUT" --workers "$WORKERS" --dense "$DENSE" --name "$NAME" > "results/build_${NAME}.log" 2>&1
echo "build exit=$?" >> "results/build_${NAME}.log"
tail -2 "results/build_${NAME}.log"
