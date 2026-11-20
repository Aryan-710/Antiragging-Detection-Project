# SafeVoice network simulation (ns-3.44)

Transport-layer study for the SafeVoice SOS alert: does an ad-hoc 802.11 mesh (AODV) with severity priority deliver critical alerts better than
(B) the same mesh without priority, (C) provisioned multi-AP Wi-Fi, (C-up) Wi-Fi whose shared uplink is congested?
The paper draft is `paper/main.tex` (compiled: `paper/main.pdf`). **All numbers in the paper come from the runs stored under `results/`.**

## Headline result (honest summary)
* Provisioned infrastructure Wi-Fi (C): 100% critical delivery at every load.
* Severity priority on the mesh (A) did **not** beat the plain mesh (B): neutral up to High load, **worse at Saturated** (68.7% vs 88.4%; 8 paired runs, unadjusted p=0.0078, Holm p=0.17).
* The mesh beat the congested-uplink infrastructure (C-up) at High/Saturated load; conclusion: dual-path design (infrastructure + mesh fallback).
* Ablation: the Saturated-load deficit tracks the EDCA mechanism; the cause is **not established**.

## Layout
| Path | Content |
|---|---|
| `sim/safevoice-sim.cc` | simulator (scenarios A/B/C, loss model, push-out queue disc, unit test `--unitTest=1`) |
| `sim/build.sh` | compile against an ns-3.44 install (`NS3_ROOT`) |
| `tools/gen_workload.py` | scenario-independent workload file + FNV-1a-64 hash |
| `tools/calibrate.py` | saturation calibration -> load levels |
| `tools/run_experiments.py`, `tools/run_all.sh` | resumable experiment runner (main, ablation, robust, confirm100, density) |
| `tools/analyze.py`, `tools/ablation_tests.py`, `tools/gen_paper_tables.py` | statistics, figures, tables |
| `tools/severity_bridge.py`, `tests/` | app score -> network class mapping and its tests |
| `../pages/2_Network_Simulation.py` | Streamlit page (multipage: appears next to `safecampus_app.py`) |
| `results/` | raw per-run folders (`runs/`), workloads, calibration, tables, figures, FlowMonitor cross-check (`xcheck/`) |

## Reproduce
1. Install ns-3.44 (here: `pip install ns3==3.44.post0`, headers/libs extracted to `$NS3_ROOT`; g++ >= 13, C++20). `bash sim/build.sh`.
2. `python3 tools/calibrate.py loads 50 60` (calibration), then `bash tools/run_all.sh` (about 1.5 h of wall time on 2 vCPUs; resumable).
3. `python3 tools/analyze.py && python3 tools/ablation_tests.py && python3 tools/gen_paper_tables.py`; then `cd paper && pdflatex main.tex` (twice).
4. Tests: `python3 -m pytest network_simulation/tests`. Sanity gates: `bash tools/gates_zero_load.sh 50`.

## Deviations from the original specification (all documented in the paper)
ns-3.44 not 3.40 (3.40 source unreachable); n=50 default (n=100 only 3 runs at High); 60 s window not 200 s; 8/6/5/3/3 runs not 30; smaller background flows;
8 APs not 4-6 (coverage hole at fewer); wired links 0.1 ms (no Internet latency); no TCP; no priority route discovery; DSDV not evaluated; no NetAnim;
no AP re-association (AP-failure result is a lower bound for C). Statistical power is limited (min exact Wilcoxon p = 0.0078 for 8 pairs, which cannot survive Holm over 12 tests).

## Validation
FlowMonitor cross-check (`results/xcheck`): SOS delivered counts match the per-packet logs exactly (B 179/195, C 195/195). Four artefacts found and fixed are in paper Section 5.
Workload hash identical across A/B/C in all 63 configurations.
