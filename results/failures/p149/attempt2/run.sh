set -u
R=<HOME>/Documents/actinv
cd $R
L=$R/target/p149-evidence/attempt2
W=$R/target/p149-evidence/work2
mkdir -p $W
: > $L/summary.txt
step() { name=$1; shift; s=$(date +%s); "$@" > $L/$name.log 2>&1; rc=$?; echo "$name rc=$rc $(( $(date +%s)-s ))s" >> $L/summary.txt; }
git rev-parse HEAD > $L/head.txt
sha256sum target/release/actinv target/release/dump target/release/p20_collapse_probe target/release/prune_probe python/target/release/libactinv.so > $L/binaries.sha256
step s5 python3 controls/p149_s5_covariance.py --work $W/s5
step s6 python3 controls/p149_s6_scaling.py --work $W/s6
step check python3 controls/check_p149.py
git status --porcelain --untracked-files=no > $L/tracked_after.txt
echo done >> $L/summary.txt
