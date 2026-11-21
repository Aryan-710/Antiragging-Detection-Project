"""Live ns-3 bridge: run the REAL simulator for one critical alert, in all four systems, and return the outcome.

Called from the Streamlit app when a report is CRITICAL. Requires the compiled simulator (sim/safevoice-sim) and the ns-3 libraries
(Linux, WSL2 or Docker). Use available() to check; otherwise the app falls back to the recorded-results demo.
One call = ONE random seed and a short window (default 20 s after a 10 s warm-up): a live illustration of the mechanism, NOT a statistical
result (the statistics come from the 208-run study).
"""
import json, os, random, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(ROOT, "sim", "safevoice-sim")
LEVELS = json.load(open(os.path.join(ROOT, "results", "calib", "n50", "load_levels.json")))["levels"]
SYSTEMS = [("A", "A", "both", ""), ("B", "B", "none", ""), ("C", "C", "none", ""), ("Cup", "C", "none", "up")]
WARMUP = 10.0


def available():
    """True if the simulator binary exists and can start (libraries found)."""
    if not os.path.exists(SIM):
        return False
    try:
        return subprocess.run([SIM, "--unitTest=1"], capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def _run_one(args):
    key, sc, pm, var, wl, run, out, uplink = args
    cmd = [SIM, f"--scenario={sc}", f"--priorityMode={pm}", f"--workload={wl}", f"--run={run}", f"--outDir={out}"]
    if var == "up":
        cmd.append(f"--uplinkRate={uplink}")
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{key}: simulator failed: {p.stderr[-300:]}")
    return key, pd.read_csv(os.path.join(out, "packets.csv"))


def run_live(load="Medium", window=20.0, seed=None, workers=None):
    """Simulate one critical SOS (injected at warm-up + 4 s from a random room on floors 1-3) in A, B, C and C-up on the same workload.
    Returns dict(alert=..., systems={key: {delivered, delay_ms, hops, crit_sent, crit_ok, all_sent, all_ok}}, seed, load, wall_s)."""
    t_start = time.time()
    seed = seed or random.randint(1000, 9_999_999)
    tmp = tempfile.mkdtemp(prefix="svlive_")
    wl = os.path.join(tmp, "wl.txt")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_workload.py"), "--run", str(seed), "--n", "50",
                    "--load", str(LEVELS[load]), "--duration", str(window), "--warmup", str(WARMUP),
                    "--burst-t", "100000", "--out", wl], check=True, capture_output=True)
    lines = open(wl).read().splitlines()
    nodes = {int(l.split()[1]): tuple(map(float, l.split()[2:5])) for l in lines if l.startswith("NODE ")}
    desk = max(nodes)
    rng = random.Random(seed)
    cands = [i for i, (x, y, z) in nodes.items() if i != desk and z >= 3.0]
    src = rng.choice(cands); t_alert = WARMUP + 4.0
    lines.append(f"SOS {t_alert} {src} 3 0")      # the user's critical alert
    open(wl, "w").write("\n".join(lines) + "\n")
    capacity = json.load(open(os.path.join(ROOT, "results", "calib", "n50", "load_levels.json")))["capacity_Mbps"]
    uplink = f"{0.3 * capacity:.4f}Mbps"
    jobs = [(k, sc, pm, var, wl, seed, os.path.join(tmp, k), uplink) for k, sc, pm, var in SYSTEMS]
    for j in jobs: os.makedirs(j[6], exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers or len(jobs)) as ex:
        res = dict(ex.map(_run_one, jobs))
    systems = {}
    for k, p in res.items():
        p = p[p.valid == 1]
        a = p[(p.src == src) & (p.sev == 3) & ((p.tx_s - t_alert).abs() < 1e-3)]
        r = a.iloc[0]; ok = r.rx_s >= 0
        c = p[p.sev == 3]
        systems[k] = dict(delivered=bool(ok), delay_ms=float((r.rx_s - r.tx_s) * 1000) if ok else None, hops=int(r.hops) if ok else None,
                          crit_sent=int(len(c)), crit_ok=int((c.rx_s >= 0).sum()), all_sent=int(len(p)), all_ok=int((p.rx_s >= 0).sum()))
    pos = nodes[src]
    return dict(alert=dict(src=src, floor=int(pos[2] // 3), x=pos[0], y=pos[1], t=t_alert), systems=systems, seed=seed, load=load,
                window=window, wall_s=round(time.time() - t_start, 1), workdir=tmp)


if __name__ == "__main__":
    ld = sys.argv[1] if len(sys.argv) > 1 else "Medium"
    print("simulator available:", available())
    print(json.dumps(run_live(ld), indent=1))
