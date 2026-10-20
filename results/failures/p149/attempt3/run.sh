set -u
R=<HOME>/Documents/actinv
cd $R
L=$R/target/p149-evidence/attempt3
W=$R/target/p149-evidence/work3
P=$R/target/p149-evidence/repeat
PW=$R/target/p149-evidence/repeat-work
mkdir -p $W $P $PW
VPY=<HOME>/.venvs/w003env/bin/python
: > $L/summary.txt
step() { name=$1; shift; s=$(date +%s); "$@" > $L/$name.log 2>&1; rc=$?; echo "$name rc=$rc $(( $(date +%s)-s ))s" >> $L/summary.txt; }
git rev-parse HEAD > $L/head.txt
sha256sum target/release/actinv target/release/dump target/release/p20_collapse_probe target/release/prune_probe python/target/release/libactinv.so $HOME/nuclear-data/alara-2.9.2-build/src/alara > $L/binaries.sha256
export ACTINV_PYTHON_LIBRARY=$R/python/target/release/libactinv.so
# attempt 3 (Amendment C)
export ACTINV_CB3_FNS_WORK=$W/cb3 ACTINV_P149_S1_WORK=$W/s1
step s0 $VPY controls/p149_s0_regenerate.py
step s6 $VPY controls/p149_s6_scaling.py --work $W/s6
step check $VPY controls/check_p149.py --out $L/check_report.json
git status --porcelain --untracked-files=no > $L/tracked_after_attempt3.txt
# repeat (Amendment C, C4)
export ACTINV_CB3_FNS_WORK=$PW/cb3 ACTINV_P149_S1_WORK=$PW/s1
step r_s0 $VPY controls/p149_s0_regenerate.py --out $P/s0
step r_s1 $VPY controls/p149_s1_fns.py --out $P/s1_fns_tendl2017_gas.json
step r_s2 $VPY controls/p149_s2_alara_nickel.py --out $P/s2_alara_nickel.json
step r_s3 $VPY controls/p149_s3_histories.py --work $PW/s3 --out $P/s3_histories.json
step r_s6 $VPY controls/p149_s6_scaling.py --work $PW/s6 --out $P/s6_scaling.json
step r_s5 $VPY controls/p149_s5_covariance.py --only ss316 --work $PW/s5 --out $P/s5_covariance.json
step r_s4 $VPY controls/p149_s4_doppler.py --work $PW/s4 --out $P/s4_doppler.json
git status --porcelain --untracked-files=no > $L/tracked_after_repeat.txt
echo done >> $L/summary.txt
