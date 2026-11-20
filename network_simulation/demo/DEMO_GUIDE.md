# Demo guide (laptop)

## A. Zero-install demo (works on Windows/Mac/Linux): show the recorded study
```
git clone https://github.com/Aryan-710/Antiragging-Detection-Project && cd Antiragging-Detection-Project
git checkout network-simulation
pip install -r requirements.txt            # app dependencies (torch/transformers are large; for a results-only demo you can skip them, see note)
streamlit run safecampus_app.py            # then open "2 Network Simulation" in the sidebar
```
Results-only shortcut (no NLP models): `pip install streamlit pandas numpy` then `streamlit run pages/2_Network_Simulation.py`.
The page shows: the severity -> class mapping, the animated run (A/B/C/C-up, same workload), main table, figures, paired tests.

## B. Re-run the simulator live (needs Linux: native Linux, WSL2 on Windows, or Docker)
1. Install ns-3.44 libraries/headers (the study used `pip download ns3==3.44.post0`, wheel unpacked to `$NS3_ROOT`; g++ >= 13).
2. `bash network_simulation/sim/build.sh`  (builds `sim/safevoice-sim`)
3. One 60 s run of each scenario on the SAME workload (takes ~20 s - 2 min each):
```
cd network_simulation; export LD_LIBRARY_PATH=$NS3_ROOT/lib
W=results/workloads/wl_n50_L5.1810_d60_b30_r1.txt
for S in A B C; do mkdir -p /tmp/demo/$S; ./sim/safevoice-sim --scenario=$S --priorityMode=$([ $S = A ] && echo both || echo none) --workload=$W --run=1 --outDir=/tmp/demo/$S; done
python3 -c "import pandas as pd;[print(s,(lambda d:(d.rx_s>=0).mean())(pd.read_csv(f'/tmp/demo/{s}/packets.csv'))) for s in 'ABC']"
```
4. Compare with the stored run folders in `network_simulation/results/runs/main/` - results are deterministic for a fixed seed, so they must match.
5. Regenerate the animation from your fresh run data: `python3 tools/make_animation.py --load High --run 1 --out results/demo/mine.mp4`
   (needs `matplotlib`; `ffmpeg` optional - falls back to GIF).

## C. Verification evidence to show (see paper Section 5)
`python3 tools/analyze.py` (hash check: A/B/C identical inputs in all 63 configs); `./sim/safevoice-sim --unitTest=1` (13 checks);
`python3 -m pytest tests` (11 tests); FlowMonitor cross-check in `results/xcheck/`.

## D. Honest framing
Provisioned Wi-Fi (C) wins at every load; priority on the mesh (A) did not beat the plain mesh (B); the mesh beats a congested uplink (C-up). Simulation, 3-8 runs per cell.
