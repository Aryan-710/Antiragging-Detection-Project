#!/usr/bin/env python3
"""
Experiment runner for the SafeVoice ns-3 study.
  * every job writes to results/runs/<exp>/<name>.tmp and is atomically renamed to <name> with a .done marker
    -> re-running the script resumes where it stopped (finished jobs are skipped, partial ones redone)
  * workloads are generated once per (n, load, run, duration) and shared by A/B/C (same file => same hash)
  * the simulator binary is called directly (no build lock), multiprocessing sized to the CPU count
Usage:
  run_experiments.py --exp main|ablation|density|robust|confirm100|all --runs N [--n 50] [--plan] [--workers K]
"""
import argparse, json, os, subprocess, sys, time, shutil, itertools
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = f"{ROOT}/sim/safevoice-sim"
RES = f"{ROOT}/results"
WARMUP = 10.0

def load_levels(n_cal=50):
    d = json.load(open(f"{RES}/calib/n{n_cal}/load_levels.json"))
    return d["capacity_Mbps"], d["levels"]

def make_job(exp, sc, pm, n, lname, lval, run, fail, var, dur, burst_t, fail_t, cap, n_cal):
    name = f"{sc}{var}_{pm}_n{n}_{lname}_{fail}_r{run}"
    up = f"{0.3 * cap:.4f}Mbps" if var == "up" else ""
    return dict(exp=exp, name=name, scenario=sc, prio=pm, n=n, load_name=lname, load=lval, run=run, failure=fail,
                variant=var, duration=dur, burst_t=burst_t, fail_t=fail_t, uplink=up)

def build_jobs(exp, runs, n, n_cal):
    cap, lv = load_levels(n_cal)
    J = []
    for r in range(1, runs + 1):
        if exp in ("main", "all"):
            for ln in ("Low", "Medium", "High", "Saturated"):
                for sc, pm, var in (("A", "both", ""), ("B", "none", ""), ("C", "none", ""), ("C", "none", "up")):
                    J.append(make_job("main", sc, pm, n, ln, lv[ln], r, "none", var, 60, 30, 0, cap, n_cal))
        if exp in ("ablation", "all"):
            for ln in ("High", "Saturated"):
                for pm in ("edca", "queue"):
                    J.append(make_job("ablation", "A", pm, n, ln, lv[ln], r, "none", "", 60, 30, 0, cap, n_cal))
        if exp in ("robust", "all"):   # 90 s window: failure at t=50 s -> 40 s before / 40 s after
            ln = "High"
            for sc, pm, var, f in (("A", "both", "", "node"), ("B", "none", "", "node"),
                                   ("C", "none", "", "ap"), ("C", "none", "up", "ap")):
                J.append(make_job("robust", sc, pm, n, ln, lv[ln], r, f, var, 90, 30, 50, cap, n_cal))
        if exp in ("density", "all"):
            for nn in (25, 75):
                for sc, pm, var in (("A", "both", ""), ("B", "none", ""), ("C", "none", ""), ("C", "none", "up")):
                    J.append(make_job("density", sc, pm, nn, "High", lv["High"], r, "none", var, 60, 30, 0, cap, n_cal))
        if exp in ("confirm100",):
            for sc, pm, var in (("A", "both", ""), ("B", "none", ""), ("C", "none", ""), ("C", "none", "up")):
                J.append(make_job("confirm100", sc, pm, 100, "High", lv["High"], r, "none", var, 60, 30, 0, cap, n_cal))
    return J

def workload_path(j):
    return f"{RES}/workloads/wl_n{j['n']}_L{j['load']:.4f}_d{int(j['duration'])}_b{int(j['burst_t'])}_r{j['run']}.txt"

def ensure_workload(j):
    """Generated serially in the parent process BEFORE the pool starts (no write races)."""
    wl = workload_path(j); os.makedirs(os.path.dirname(wl), exist_ok=True)
    if not os.path.exists(wl):
        subprocess.run([sys.executable, f"{ROOT}/tools/gen_workload.py", "--run", str(j["run"]), "--n", str(j["n"]),
                        "--load", f"{j['load']:.4f}", "--duration", str(j["duration"]), "--burst-t", str(j["burst_t"]),
                        "--out", wl + ".part"], check=True, capture_output=True)
        os.replace(wl + ".part", wl)

def run_job(j):
    out = f"{RES}/runs/{j['exp']}/{j['name']}"
    if os.path.exists(f"{out}/.done"):
        return j["name"], "skip", 0.0
    tmp = out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    wl = workload_path(j)
    cmd = [SIM, f"--scenario={j['scenario']}", f"--priorityMode={j['prio']}", f"--workload={wl}",
           f"--run={j['run']}", f"--outDir={tmp}", f"--failure={j['failure']}", f"--failTime={j['fail_t']}"]
    if j["uplink"]:
        cmd.append(f"--uplinkRate={j['uplink']}")
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.time() - t0
    if p.returncode != 0:
        open(f"{tmp}/FAILED.txt", "w").write(p.stdout + "\n" + p.stderr)
        return j["name"], "FAILED", dt
    json.dump(j, open(f"{tmp}/job.json", "w"))
    open(f"{tmp}/.done", "w").write("ok")
    shutil.rmtree(out, ignore_errors=True)
    os.rename(tmp, out)
    return j["name"], "ok", dt

# rough per-run cost in seconds for the plan printout (measured in pilots; edit after pilots)
COST = {("A", "Low"): 25, ("B", "Low"): 25, ("A", "Medium"): 45, ("B", "Medium"): 45, ("A", "High"): 80, ("B", "High"): 80,
        ("A", "Saturated"): 110, ("B", "Saturated"): 110}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="main"); ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--n", type=int, default=50); ap.add_argument("--n-cal", type=int, default=50)
    ap.add_argument("--workers", type=int, default=len(os.sched_getaffinity(0)))
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()
    jobs = build_jobs(a.exp, a.runs, a.n, a.n_cal)
    todo = [j for j in jobs if not os.path.exists(f"{RES}/runs/{j['exp']}/{j['name']}/.done")]
    est = sum((COST.get((j["scenario"], j["load_name"]), 12) * (j["n"] / 50) ** 2.0 * (j["duration"] + WARMUP) / 70) if j["scenario"] != "C" else 12 * (j["duration"] + WARMUP) / 70 * (j["n"] / 50)
              for j in todo)
    print(f"{len(jobs)} jobs ({len(todo)} to do); projected CPU time ~{est/3600:.2f} h on 1 core -> ~{est/3600/a.workers:.2f} h with {a.workers} worker(s)", flush=True)
    if a.plan: return
    for j in todo: ensure_workload(j)
    t0 = time.time(); done = 0
    with Pool(a.workers) as pool:
        for name, st, dt in pool.imap_unordered(run_job, todo):
            done += 1
            print(f"[{done}/{len(todo)}] {name}: {st} {dt:.0f}s  (elapsed {(time.time()-t0)/60:.1f} min)", flush=True)

if __name__ == "__main__":
    main()
