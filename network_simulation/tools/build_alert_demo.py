#!/usr/bin/env python3
"""Build demo/alert_demo.html: a single self-contained page (no install, opens in any browser).
Data embedded = every CRITICAL SOS packet recorded in the main grid (n=50, all runs) per system and load: [hops (-1 = lost), delay_ms]."""
import json, glob, os, re
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = {"A": "A_both", "B": "B_none", "C": "C_none", "Cup": "Cup_none"}
LOADS = ["Low", "Medium", "High", "Saturated"]
data = {}
for ld in LOADS:
    data[ld] = {}
    for k, pre in SC.items():
        rows = []
        for d in sorted(glob.glob(f"{ROOT}/results/runs/main/{pre}_n50_{ld}_none_r*")):
            if d.endswith(".tmp") or not os.path.exists(d + "/packets.csv"): continue
            p = pd.read_csv(d + "/packets.csv"); p = p[(p.sev == 3) & (p.valid == 1)]
            for _, r in p.iterrows():
                ok = r.rx_s >= 0
                rows.append([int(r.hops) if ok else -1, round((r.rx_s - r.tx_s) * 1000, 1) if ok else -1])
        data[ld][k] = rows
wl = glob.glob(f"{ROOT}/results/workloads/wl_n50_*_r1.txt")[0]
nodes = [tuple(map(float, l.split()[2:5])) for l in open(wl) if l.startswith("NODE ")]
tpl = open(f"{ROOT}/tools/alert_demo_template.html").read()
html = tpl.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))).replace("/*__NODES__*/null", json.dumps([[round(a, 1), round(b, 1), round(c, 1)] for a, b, c in nodes], separators=(",", ":")))
os.makedirs(f"{ROOT}/demo", exist_ok=True)
open(f"{ROOT}/demo/alert_demo.html", "w").write(html)
print("wrote demo/alert_demo.html", len(html) // 1024, "KB;", {ld: {k: len(v) for k, v in data[ld].items()} for ld in ["High"]})
