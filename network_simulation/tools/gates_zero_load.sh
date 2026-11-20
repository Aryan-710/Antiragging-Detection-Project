#!/usr/bin/env bash
# Gates G3-G7 at zero background load: 60 s window, runs 1..3, A (both/edca/queue/none), B, C. N=${1:-50}
set -e
cd "$(dirname "$0")/.."
N=${1:-50}; OUT=results/gates/zero_load_n$N; mkdir -p $OUT
for r in 1 2 3; do
  python3 tools/gen_workload.py --run $r --n $N --load 0 --duration 60 --burst-t 30 --out $OUT/wl_r$r.txt > /dev/null
  for cfg in "A both" "A none" "A edca" "A queue" "B none" "C none"; do
    set -- $cfg; sc=$1; pm=$2; d=$OUT/${sc}_${pm}_r$r; mkdir -p $d
    ./sim/safevoice-sim --scenario=$sc --priorityMode=$pm --workload=$OUT/wl_r$r.txt --run=$r --outDir=$d --acStats=1 | grep -E "DONE|AC-STATS" | sed "s/^/r$r /"
  done
done
