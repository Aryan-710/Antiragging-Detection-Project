#!/usr/bin/env bash
# Full study in priority order (every step is resumable; re-run this script after any interruption).
set -e
cd "$(dirname "$0")/.."
python3 tools/run_experiments.py --exp main       --runs 8   # A/B/C/C-up x 4 loads            (core result)
python3 tools/run_experiments.py --exp ablation   --runs 6   # EDCA-only / queue-only          (which mechanism helps)
python3 tools/run_experiments.py --exp robust     --runs 5   # 10% node failure / 1 AP failure
python3 tools/run_experiments.py --exp confirm100 --runs 3   # n = 100 at High load (spec scale)
python3 tools/run_experiments.py --exp density    --runs 3   # n = 25 and 75 at High load
