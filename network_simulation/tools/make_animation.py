#!/usr/bin/env python3
"""Animate one run of the study from the REAL run data: node positions/mobility come from the workload file,
SOS packet events (send, delivered, lost) come from each scenario's packets.csv.
Side-by-side A / B / C / C-up on the same workload.  Straight lines are the logical source->desk path
(per-hop routes are not logged); the hop count of each delivered packet is printed next to it.
usage: python3 tools/make_animation.py --load Saturated --run 1 --out results/demo/saturated_r1.mp4
"""
import argparse, glob, os, sys
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import animation

ap = argparse.ArgumentParser()
ap.add_argument("--load", default="Saturated"); ap.add_argument("--run", type=int, default=1)
ap.add_argument("--n", type=int, default=50); ap.add_argument("--out", default="results/demo/demo.mp4")
ap.add_argument("--fps", type=int, default=12); ap.add_argument("--dt", type=float, default=0.25, help="sim seconds per frame")
ap.add_argument("--runs-dir", default="results/runs/main"); ap.add_argument("--t0", type=float, default=10.0)
ap.add_argument("--t1", type=float, default=70.0)
a = ap.parse_args()

SC = [("A", "A_both", "A: mesh + severity priority"), ("B", "B_none", "B: mesh, no priority"),
      ("C", "C_none", "C: infrastructure Wi-Fi"), ("Cup", "Cup_none", "C-up: congested uplink")]
def rd(prefix):
    d = f"{a.runs_dir}/{prefix}_n{a.n}_{a.load}_none_r{a.run}"
    if not os.path.exists(d + "/packets.csv"): sys.exit(f"missing {d}/packets.csv (run the experiment first)")
    return pd.read_csv(d + "/packets.csv")
P = {k: rd(p) for k, p, _ in SC}
wlf = glob.glob(f"results/workloads/wl_n{a.n}_*_r{a.run}.txt")
# pick the workload whose load matches the run (the load level appears in the run's job.json)
import json
jf = f"{a.runs_dir}/A_both_n{a.n}_{a.load}_none_r{a.run}/job.json"
wl = None
if os.path.exists(jf):
    j = json.load(open(jf)); wl = j.get("workload") or j.get("wl")
if not wl or not os.path.exists(wl): wl = wlf[0]
nodes, wpts = {}, {}
for ln in open(wl):
    t = ln.split()
    if t and t[0] == "NODE": nodes[int(t[1])] = tuple(map(float, t[2:5])) + (int(t[5]),)
    elif t and t[0] == "WPT": wpts.setdefault(int(t[1]), []).append(tuple(map(float, t[2:6])))
desk = max(nodes)
def pos(i, t):
    x, y, z, mob = nodes[i]
    if mob and i in wpts:
        w = wpts[i]
        if t <= w[0][0]: return w[0][1], w[0][2], w[0][3]
        for p, q in zip(w, w[1:]):
            if p[0] <= t <= q[0]:
                f = (t - p[0]) / max(q[0] - p[0], 1e-9); return p[1] + f * (q[1] - p[1]), p[2] + f * (q[2] - p[2]), p[3]
        return w[-1][1], w[-1][2], w[-1][3]
    return x, y, z
FH = 17.0   # vertical spacing between floors in the plot
def xy(i, t):
    x, y, z = pos(i, t); fl = int(z // 3); return x, y + fl * FH
COL = {3: "#d62728", 2: "#ff7f0e", 1: "#7f7f7f"}
fig, axs = plt.subplots(1, 4, figsize=(15, 8.6), dpi=100)
fig.subplots_adjust(left=0.01, right=0.99, top=0.88, bottom=0.07, wspace=0.06)
fig.suptitle(f"SafeVoice SOS delivery - same workload (run {a.run}, load {a.load}, n={a.n}); data from executed ns-3 runs", fontsize=12, y=0.985)
for ax, (k, _, title) in zip(axs, SC):
    ax.set_xlim(-1, 41); ax.set_ylim(-1, 4 * FH - 1); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    for f in range(4):
        ax.add_patch(plt.Rectangle((0, f * FH), 40, 15, fill=False, ec="#999", lw=1))
        ax.add_patch(plt.Rectangle((0, f * FH + 6), 40, 3, fc="#eef", ec="none", zorder=0))
        ax.text(41, f * FH + 7.5, f"floor {f}", fontsize=8, va="center", ha="right", color="#666")
    ax.set_title(title, fontsize=11)
scat = []; txt = []; dyn = [[] for _ in SC]
mob_ids = [i for i in nodes if nodes[i][3] and i != desk]
for ax in axs:
    xs = [xy(i, a.t0)[0] for i in nodes if i != desk]; ys = [xy(i, a.t0)[1] for i in nodes if i != desk]
    s = ax.scatter(xs, ys, s=14, c="#1f77b4", zorder=3); scat.append(s)
    ax.scatter(*zip(xy(desk, 0)), s=120, marker="s", c="k", zorder=4)
    txt.append(ax.text(0.5, -0.015, "", transform=ax.transAxes, ha="center", va="top", fontsize=10))
tt = fig.text(0.5, 0.915, "", ha="center", fontsize=12)
ids = [i for i in nodes if i != desk]
frames = np.arange(a.t0, a.t1, a.dt)
def upd(t):
    tt.set_text(f"simulation time {t - a.t0:5.1f} s   (burst of 20 SOS at t = 30 s)   red = critical, orange = high, grey = low; X = not delivered")
    for si, (k, _, _) in enumerate(SC):
        ax = axs[si]
        for art in dyn[si]: art.remove()
        dyn[si].clear()
        scat[si].set_offsets(np.array([xy(i, t) for i in ids]))
        d = P[k]; sent = d[(d.tx_s <= t)]
        crit = sent[sent.sev == 3]; dc = crit[(crit.rx_s >= 0) & (crit.rx_s <= t)]
        for _, r in sent[(sent.tx_s > t - 1.0)].iterrows():
            x, y = xy(int(r.src), r.tx_s); c = COL[int(r.sev)]
            dyn[si].append(ax.scatter([x], [y], s=70 + 60 * (r.sev == 3), facecolors="none", edgecolors=c, lw=2, zorder=5))
        for _, r in d[(d.rx_s >= 0) & (d.rx_s <= t) & (d.rx_s > t - 0.7)].iterrows():
            x, y = xy(int(r.src), r.tx_s); dx, dy = xy(desk, 0)
            dyn[si].extend(ax.plot([x, dx], [y, dy], color=COL[int(r.sev)], lw=1.6, alpha=0.8, zorder=2))
            if k in ("A", "B"): dyn[si].append(ax.text((x + dx) / 2, (y + dy) / 2, f"{int(r.hops)} hop(s)", fontsize=7, color=COL[int(r.sev)]))
        lost = d[(d.rx_s < 0) & (d.tx_s <= t - 6.0) & (d.tx_s > t - 9.0)]   # shown ~6 s after send: no delivery would still arrive (delays << 6 s)
        for _, r in lost.iterrows():
            x, y = xy(int(r.src), r.tx_s)
            dyn[si].append(ax.scatter([x], [y], s=60, marker="x", c=COL[int(r.sev)], lw=2, zorder=6))
        cs = len(crit); cd = len(dc); allsent = len(sent); alld = len(sent[(sent.rx_s >= 0) & (sent.rx_s <= t)])
        txt[si].set_text(f"critical delivered {cd}/{cs}   |   all SOS {alld}/{allsent}")
    return []
os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
anim = animation.FuncAnimation(fig, upd, frames=frames, blit=False)
try:
    anim.save(a.out, writer=animation.FFMpegWriter(fps=a.fps, bitrate=2400))
except Exception as e:
    out = os.path.splitext(a.out)[0] + ".gif"; print("ffmpeg unavailable (%s); writing GIF %s" % (e, out))
    anim.save(out, writer=animation.PillowWriter(fps=a.fps))
print("wrote", a.out)
