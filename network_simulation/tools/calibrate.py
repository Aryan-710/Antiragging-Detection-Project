#!/usr/bin/env python3
"""G8: saturation-capacity pilot for the 100-node ad-hoc mesh (scenario B, no priority).
Offers increasing aggregate background load and measures delivered background goodput.
Capacity = max delivered goodput over the sweep; load levels are then 10/40/80/120 % of it."""
import subprocess, sys, os, json, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = f"{ROOT}/results/calib"; os.makedirs(OUT, exist_ok=True)
loads = [float(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else "0.5,1,2,3,4,6,8".split(","))]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 100
WIN = sys.argv[3] if len(sys.argv) > 3 else "40"
OUT = f"{OUT}/n{N}"; os.makedirs(OUT, exist_ok=True)
rows = []
for L in loads:
    wl = f"{OUT}/wl_L{L}.txt"; d = f"{OUT}/B_L{L}"; os.makedirs(d, exist_ok=True)
    subprocess.run(["python3", f"{ROOT}/tools/gen_workload.py", "--run", "1", "--n", str(N), "--load", str(L),
                    "--duration", WIN, "--burst-t", str(10 + float(WIN) / 2), "--out", wl], check=True, capture_output=True)
    subprocess.run([f"{ROOT}/sim/safevoice-sim", "--scenario=B", f"--workload={wl}", "--run=1", f"--outDir={d}"],
                   check=True, capture_output=True)
    s = pd.read_csv(f"{d}/summary.csv").iloc[0]
    dur = s.duration
    rows.append(dict(offered_Mbps=L, tx_Mbps=s.bgTxBytes * 8 / dur / 1e6, goodput_Mbps=s.bgRxBytes * 8 / dur / 1e6,
                     bg_pdr=s.bgRxBytes / max(s.bgTxBytes, 1), sos_pdr=(s.sosRx1 + s.sosRx2 + s.sosRx3) / max(s.sosTx1 + s.sosTx2 + s.sosTx3, 1),
                     wall_s=s.wallclock_s))
    print(rows[-1], flush=True)
df = pd.DataFrame(rows); df.to_csv(f"{OUT}/calibration.csv", index=False)
cap = df.goodput_Mbps.max()
json.dump(dict(capacity_Mbps=cap, levels={k: round(cap * f, 3) for k, f in dict(Low=.10, Medium=.40, High=.80, Saturated=1.20).items()}),
          open(f"{OUT}/load_levels.json", "w"), indent=1)
print("capacity (max goodput) =", round(cap, 3), "Mbps"); print(open(f"{OUT}/load_levels.json").read())
