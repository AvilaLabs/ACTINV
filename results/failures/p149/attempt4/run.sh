set -u
R=<HOME>/Documents/actinv
cd $R
L=$R/target/p149-evidence/attempt4
W=$R/target/p149-evidence/work4
P=$R/target/p149-evidence/repeat
PW=$R/target/p149-evidence/repeat-work4
mkdir -p $W $PW
VPY=<HOME>/.venvs/w003env/bin/python
: > $L/summary.txt
step() { name=$1; shift; s=$(date +%s); "$@" > $L/$name.log 2>&1; rc=$?; echo "$name rc=$rc $(( $(date +%s)-s ))s" >> $L/summary.txt; }
git rev-parse HEAD > $L/head.txt
sha256sum target/release/actinv target/release/dump target/release/p20_collapse_probe target/release/prune_probe python/target/release/libactinv.so $HOME/nuclear-data/alara-2.9.2-build/src/alara > $L/binaries.sha256
export ACTINV_PYTHON_LIBRARY=$R/python/target/release/libactinv.so
export ACTINV_P12_FNG_ROOT=$R/target/p149-fng
# superseded partial S6 records (preserved in results/failures/p149/attempt3 and target/p149-evidence/attempt3)
mv results/p149/s6_scaling.json $R/target/p149-evidence/attempt3/s6_scaling_evidence_partial.json
mv $P/s6_scaling.json $R/target/p149-evidence/attempt3/s6_scaling_repeat_partial.json 2>/dev/null
# attempt 4 (Amendment D)
export ACTINV_CB3_FNS_WORK=$W/cb3 ACTINV_P149_S1_WORK=$W/s1 ACTINV_P9_WORK=$HOME/.p9a
step s0 $VPY controls/p149_s0_regenerate.py
step s6 $VPY controls/p149_s6_scaling.py --work $W/s6
step check $VPY controls/check_p149.py --out $L/check_report.json
git status --porcelain --untracked-files=no > $L/tracked_after_attempt4.txt
# repeat of S0 and S6 (Amendment D, D4)
export ACTINV_CB3_FNS_WORK=$PW/cb3 ACTINV_P149_S1_WORK=$PW/s1 ACTINV_P9_WORK=$HOME/.p9b
step r_s0 $VPY controls/p149_s0_regenerate.py --out $P/s0
step r_s6 $VPY controls/p149_s6_scaling.py --work $PW/s6 --out $P/s6_scaling.json
git status --porcelain --untracked-files=no > $L/tracked_after_repeat.txt
echo done >> $L/summary.txt
