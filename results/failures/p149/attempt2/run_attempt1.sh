set -u
R=<HOME>/Documents/actinv
cd $R
L=$R/target/p149-evidence
W=$R/target/p149-evidence/work
mkdir -p $W
VPY=<HOME>/.venvs/w003env/bin/python
: > $L/summary.txt
step() { name=$1; shift; s=$(date +%s); "$@" > $L/$name.log 2>&1; rc=$?; echo "$name rc=$rc $(( $(date +%s)-s ))s" >> $L/summary.txt; }
git rev-parse HEAD > $L/head.txt
step build_ws cargo build --release --workspace
step build_py cargo build --release --manifest-path python/Cargo.toml
sha256sum target/release/actinv target/release/dump target/release/p20_collapse_probe target/release/prune_probe python/target/release/libactinv.so > $L/binaries.sha256
export ACTINV_PYTHON_LIBRARY=$R/python/target/release/libactinv.so
export ACTINV_CB3_FNS_WORK=$W/cb3
export ACTINV_P149_S1_WORK=$W/s1
step s0 python3 controls/p149_s0_regenerate.py
step s1 python3 controls/p149_s1_fns.py
step s2 $VPY controls/p149_s2_alara_nickel.py
step s3 $VPY controls/p149_s3_histories.py --work $W/s3
step s4 $VPY controls/p149_s4_doppler.py --work $W/s4
step s5 python3 controls/p149_s5_covariance.py --work $W/s5
step s6 python3 controls/p149_s6_scaling.py --work $W/s6
step check python3 controls/check_p149.py
git status --porcelain --untracked-files=no > $L/tracked_after.txt
echo done >> $L/summary.txt
