#!/usr/bin/env python3
"""
SafeVoice analysis: per-run metrics -> mean +/- 95% CI (Student t over runs) -> paired Wilcoxon (+Holm) -> figures/tables.
All numbers come from results/runs/*/*/{summary.csv,packets.csv}; nothing is hard-coded.
"""
import glob, json, os, sys, itertools
import numpy as np, pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = f"{ROOT}/results"; FIG = f"{RES}/figures"; TAB = f"{RES}/tables"
os.makedirs(FIG, exist_ok=True); os.makedirs(TAB, exist_ok=True)
LEVELS = ["Low", "Medium", "High", "Saturated"]
# fixed colour per scenario (Okabe-Ito, colour-blind safe) -- same everywhere
COL = {"A": "#0072B2", "B": "#E69F00", "C": "#009E73", "Cup": "#CC79A7",
       "A-edca": "#56B4E9", "A-queue": "#D55E00"}
LAB = {"A": "A  ad-hoc + severity priority", "B": "B  ad-hoc, FIFO (no priority)",
       "C": "C  Wi-Fi, provisioned", "Cup": "C-up  Wi-Fi, congested uplink",
       "A-edca": "A: EDCA only", "A-queue": "A: queue only"}
plt.rcParams.update({"figure.dpi": 130, "savefig.dpi": 220, "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": .25, "legend.frameon": False})

def tag(j):   # scenario key used in plots
    if j["scenario"] == "A":
        return {"both": "A", "edca": "A-edca", "queue": "A-queue", "none": "B"}[j["prio"]]
    return "B" if j["scenario"] == "B" else ("Cup" if j["variant"] == "up" else "C")

def jitter(d):  # mean |delay_i - delay_{i-1}| in transmit order (RFC 3550 style)
    return float(np.mean(np.abs(np.diff(d)))) if len(d) > 1 else np.nan

def cls_metrics(p, prefix):
    tx = len(p); ok = p[p.rx_s >= 0]; d = (ok.rx_s - ok.tx_s).values
    return {f"{prefix}_tx": tx, f"{prefix}_rx": len(ok), f"{prefix}_pdr": len(ok) / tx if tx else np.nan,
            f"{prefix}_dmean": d.mean() if len(d) else np.nan, f"{prefix}_dmed": np.median(d) if len(d) else np.nan,
            f"{prefix}_dp95": np.percentile(d, 95) if len(d) else np.nan,
            f"{prefix}_jit": jitter((ok.sort_values("tx_s").rx_s - ok.sort_values("tx_s").tx_s).values),
            f"{prefix}_ok2": (d <= 2).sum() / tx if tx else np.nan, f"{prefix}_ok5": (d <= 5).sum() / tx if tx else np.nan}

def run_metrics(d):
    j = json.load(open(f"{d}/job.json")); s = pd.read_csv(f"{d}/summary.csv").iloc[0]
    p = pd.read_csv(f"{d}/packets.csv"); p = p[p.valid == 1]
    row = dict(exp=j["exp"], key=tag(j), scenario=j["scenario"], prio=j["prio"], variant=j["variant"], n=j["n"],
               load_name=j["load_name"], load=j["load"], run=j["run"], failure=j["failure"], fail_t=j["fail_t"],
               burst_t=j["burst_t"], hash=s.workloadHash, wall=s.wallclock_s)
    row.update(cls_metrics(p, "all"))
    for sv in (1, 2, 3): row.update(cls_metrics(p[p.sev == sv], f"s{sv}"))
    b = p[(p.burst == 1)]; bc = b[b.sev == 3]; okb = bc[bc.rx_s >= 0]
    row["burst_pdr"] = (b.rx_s >= 0).mean() if len(b) else np.nan
    row["burstcrit_pdr"] = (bc.rx_s >= 0).mean() if len(bc) else np.nan
    row["burstcrit_ttf"] = (okb.rx_s.min() - j["burst_t"]) if len(okb) else np.nan   # includes generation time (identical across scenarios)
    row["burstcrit_dmed"] = float(np.median(okb.rx_s - okb.tx_s)) if len(okb) else np.nan  # pure network delay of delivered critical burst SOS
    if j["failure"] != "none":
        for nm, m in (("pre", p.tx_s < j["fail_t"]), ("post", p.tx_s >= j["fail_t"])):
            row[f"{nm}_pdr"] = (p[m].rx_s >= 0).mean(); row[f"{nm}_crit_pdr"] = (p[m & (p.sev == 3)].rx_s >= 0).mean()
    ok = p[p.rx_s >= 0]
    row["hops_mean"] = ok[ok.hops > 0].hops.mean() if (ok.hops > 0).any() else np.nan
    dur = s.duration
    row["bg_goodput"] = s.bgRxBytes * 8 / dur / 1e6; row["bg_tx"] = s.bgTxBytes * 8 / dur / 1e6
    row["bg_pdr"] = s.bgRxBytes / s.bgTxBytes if s.bgTxBytes else np.nan
    row["ctrl_pps"] = s.ctrlPkts / dur; row["ctrl_kbps"] = s.ctrlBytes * 8 / dur / 1e3
    for c in ("tcOverflow", "tcPushout", "macQueueFull", "macExpired", "macRetryLimit", "noRoute", "warmReached", "assocAtWarm"):
        row[c] = s[c]
    return row, p

def ci95(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    if len(x) < 2: return (x.mean() if len(x) else np.nan, np.nan, len(x))
    return x.mean(), stats.t.ppf(.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)), len(x)

def holm(pvals):
    idx = np.argsort(pvals); m = len(pvals); adj = np.empty(m); run = 0.0
    for r, i in enumerate(idx):
        run = max(run, (m - r) * pvals[i]); adj[i] = min(1.0, run)
    return adj

def paired_test(x, y):
    d = np.asarray(x, float) - np.asarray(y, float); ok = ~np.isnan(d); d = d[ok]
    if len(d) == 0 or np.all(d == 0): return dict(n_pairs=len(d), p=np.nan, r=0.0, note="no difference (all paired differences zero)", mdiff=0.0)
    nz = d[d != 0]; ranks = stats.rankdata(np.abs(nz)); wp = ranks[nz > 0].sum(); wm = ranks[nz < 0].sum()
    p = stats.wilcoxon(nz, zero_method="wilcox", method="auto").pvalue
    return dict(n_pairs=len(d), p=p, r=(wp - wm) / (wp + wm), note="", mdiff=d.mean())

def main():
    dirs = sorted(d for d in glob.glob(f"{RES}/runs/*/*") if os.path.exists(f"{d}/.done"))
    if not dirs: sys.exit("no finished runs")
    rows, pk = [], []
    for d in dirs:
        r, p = run_metrics(d); rows.append(r)
        p = p.assign(exp=r["exp"], key=r["key"], n=r["n"], load_name=r["load_name"], run=r["run"], failure=r["failure"], burst_t=r["burst_t"]); pk.append(p)
    R = pd.DataFrame(rows); P = pd.concat(pk); R.to_csv(f"{TAB}/per_run_metrics.csv", index=False)
    # integrity: same (exp,n,load,failure,run) -> same workload hash across scenarios
    g = R.groupby(["exp", "n", "load_name", "failure", "run"]).hash.nunique()
    print("workload-hash check: configs where A/B/C differ =", int((g > 1).sum()), "of", len(g))
    # ---------------- summary table ----------------
    metrics = ["s3_pdr", "s3_ok2", "s3_ok5", "s3_dmed", "s3_dp95", "s3_jit", "s2_pdr", "s2_dp95", "s1_pdr", "all_pdr", "all_dp95",
               "burstcrit_pdr", "burstcrit_ttf", "burstcrit_dmed", "bg_goodput", "bg_pdr", "ctrl_pps", "hops_mean", "tcOverflow", "tcPushout",
               "macQueueFull", "macExpired", "macRetryLimit", "noRoute"]
    out = []
    for (e, k, n, ln, f), g in R.groupby(["exp", "key", "n", "load_name", "failure"]):
        row = dict(exp=e, key=k, n=n, load=ln, failure=f, runs=len(g), offered_Mbps=g.load.iloc[0])
        for m in metrics:
            mu, h, c = ci95(g[m]); row[m] = mu; row[m + "_ci"] = h
        out.append(row)
    S = pd.DataFrame(out); S.to_csv(f"{TAB}/summary_mean_ci.csv", index=False)
    # ---------------- paired tests ----------------
    T = []
    for (e, n, ln, f), g in R.groupby(["exp", "n", "load_name", "failure"]):
        pv = {k: gg.set_index("run") for k, gg in g.groupby("key")}
        for a, b in (("A", "B"), ("A", "C"), ("A", "Cup"), ("A", "A-edca"), ("A", "A-queue"), ("A-edca", "B"), ("A-queue", "B")):
            if a in pv and b in pv:
                common = pv[a].index.intersection(pv[b].index)
                for m in ("s3_pdr", "s3_ok2", "s3_dp95", "s2_pdr", "all_pdr"):
                    t = paired_test(pv[a].loc[common, m], pv[b].loc[common, m])
                    T.append(dict(exp=e, n=n, load=ln, failure=f, A=a, B=b, metric=m, **t))
    TT = pd.DataFrame(T)
    if len(TT):
        TT["p_holm"] = np.nan
        for m, g in TT.groupby("metric"):
            ok = g.p.notna()
            if ok.any(): TT.loc[g.index[ok], "p_holm"] = holm(g.p[ok].values)
        TT.to_csv(f"{TAB}/paired_tests.csv", index=False)
    # ---------------- figures ----------------
    def lines(exp, metric, ylabel, fname, keys=("A", "B", "C", "Cup"), title=None, pct=False):
        g = S[(S.exp == exp) & (S.failure == "none")]
        if g.empty: return
        fig, ax = plt.subplots(figsize=(4.6, 3.2))
        for k in keys:
            h = g[g.key == k].set_index("load").reindex(LEVELS)
            if h[metric].notna().any():
                y = h[metric] * (100 if pct else 1); e = h[metric + "_ci"] * (100 if pct else 1)
                ax.errorbar(range(4), y, yerr=e, color=COL[k], marker="o", ms=4, capsize=3, lw=1.5, label=LAB[k])
        offs = g[g.key == "B"].set_index("load").reindex(LEVELS).offered_Mbps
        ax.set_xticks(range(4)); ax.set_xticklabels([f"{l}\n({o:.1f} Mbps)" if pd.notna(o) else l for l, o in zip(LEVELS, offs)])
        ax.set_ylabel(ylabel); ax.set_xlabel("background load"); ax.legend(fontsize=7)
        if title: ax.set_title(title, fontsize=9)
        fig.tight_layout(); fig.savefig(f"{FIG}/{fname}.png"); fig.savefig(f"{FIG}/{fname}.pdf"); plt.close(fig)
    lines("main", "s3_pdr", "critical SOS delivery ratio (%)", "fig1_crit_pdr_vs_load", pct=True)
    lines("main", "s3_ok2", "critical SOS delivered within 2 s (%)", "fig2_crit_deadline2s_vs_load", pct=True)
    lines("main", "s3_dp95", "95th-pct delay of delivered critical SOS (s)", "fig3_crit_delay_p95_vs_load")
    lines("main", "all_pdr", "all-SOS delivery ratio (%)", "fig3b_all_pdr_vs_load", pct=True)
    lines("main", "bg_goodput", "background goodput (Mbps)", "fig9_bg_goodput_vs_load")
    lines("main", "ctrl_pps", "routing control packets / s (AODV)", "fig10_aodv_overhead", keys=("A", "B"))
    # delay CDFs (undelivered counted as never) per severity at High
    for exp, ln in (("main", "High"), ("main", "Saturated")):
        Q = P[(P.exp == exp) & (P.load_name == ln) & (P.failure == "none")]
        if Q.empty: continue
        fig, axs = plt.subplots(1, 3, figsize=(9.5, 3), sharey=True)
        for ax, sv, nm in zip(axs, (3, 2, 1), ("critical (3)", "high (2)", "low (1)")):
            for k in ("A", "B", "C", "Cup"):
                q = Q[(Q.key == k) & (Q.sev == sv)]
                if q.empty: continue
                d = np.sort((q.rx_s - q.tx_s)[q.rx_s >= 0].values); y = np.arange(1, len(d) + 1) / len(q)
                ax.step(np.r_[0.001, d, 30], np.r_[0, y, y[-1] if len(y) else 0], where="post", color=COL[k], lw=1.5, label=LAB[k])
            ax.set_xscale("log"); ax.set_xlim(.003, 30); ax.set_title(f"severity {nm}", fontsize=9); ax.set_xlabel("end-to-end delay (s)")
        axs[0].set_ylabel("fraction of SENT packets delivered within t"); axs[0].legend(fontsize=6.5, loc="upper left")
        fig.suptitle(f"Delay CDF, {ln} load (plateau = delivery ratio)", fontsize=9)
        fig.tight_layout(); fig.savefig(f"{FIG}/fig4_delay_cdf_{ln}.png"); fig.savefig(f"{FIG}/fig4_delay_cdf_{ln}.pdf"); plt.close(fig)
    # density
    D = S[(S.exp.isin(["density", "confirm100"]) | ((S.exp == "main") & (S.load == "High"))) & (S.load == "High") & (S.failure == "none")]
    if D.n.nunique() > 1:
        fig, axs = plt.subplots(1, 2, figsize=(8, 3))
        for ax, m, yl in zip(axs, ("s3_pdr", "s3_dp95"), ("critical SOS delivery ratio (%)", "p95 delay critical SOS (s)")):
            for k in ("A", "B", "C", "Cup"):
                h = D[D.key == k].groupby("n", as_index=False).first().sort_values("n")
                if len(h):
                    f = 100 if m.endswith("pdr") else 1
                    ax.errorbar(h.n, h[m] * f, yerr=h[m + "_ci"] * f, color=COL[k], marker="o", ms=4, capsize=3, label=LAB[k])
            ax.set_xlabel("number of student nodes (same absolute offered load)"); ax.set_ylabel(yl)
        axs[0].legend(fontsize=7); fig.tight_layout(); fig.savefig(f"{FIG}/fig5_density.png"); fig.savefig(f"{FIG}/fig5_density.pdf"); plt.close(fig)
    # burst timeline
    Qb = P[(P.exp == "main") & (P.load_name.isin(["High", "Saturated"])) & (P.failure == "none")]
    Qb = Qb[(Qb.sev == 3) & (Qb.tx_s >= Qb.burst_t) & (Qb.tx_s <= Qb.burst_t + 5)]
    if len(Qb):
        fig, axs = plt.subplots(1, 2, figsize=(8, 3), sharey=True)
        for ax, ln in zip(axs, ("High", "Saturated")):
            for k in ("A", "B", "C", "Cup"):
                q = Qb[(Qb.key == k) & (Qb.load_name == ln)]
                if q.empty: continue
                t = np.sort((q.rx_s - q.burst_t)[q.rx_s >= 0].values); y = np.arange(1, len(t) + 1) / len(q)
                ax.step(np.r_[0, t, 20], np.r_[0, y, y[-1] if len(y) else 0], where="post", color=COL[k], label=LAB[k])
            ax.set_title(f"{ln} load", fontsize=9); ax.set_xlabel("time since crisis start (s)")
        axs[0].set_ylabel("fraction of critical burst SOS delivered"); axs[0].legend(fontsize=6.5)
        fig.tight_layout(); fig.savefig(f"{FIG}/fig6_burst_timeline.png"); fig.savefig(f"{FIG}/fig6_burst_timeline.pdf"); plt.close(fig)
    # ablation
    Ab = S[(S.exp.isin(["ablation", "main"])) & (S.key.isin(["B", "A-edca", "A-queue", "A"])) & (S.load.isin(["High", "Saturated"])) & (S.failure == "none")]
    if Ab.key.nunique() > 2:
        fig, axs = plt.subplots(1, 2, figsize=(8, 3), sharey=True); order = ["B", "A-edca", "A-queue", "A"]
        for ax, ln in zip(axs, ("High", "Saturated")):
            g = Ab[Ab.load == ln].set_index("key").reindex(order); x = np.arange(len(order)); w = .38
            ax.bar(x - w / 2, g.s3_pdr * 100, w, yerr=g.s3_pdr_ci * 100, color=[COL[k] for k in order], capsize=3, label="critical (3)")
            ax.bar(x + w / 2, g.s2_pdr * 100, w, yerr=g.s2_pdr_ci * 100, color=[COL[k] for k in order], alpha=.45, capsize=3, label="high (2)")
            ax.set_xticks(x); ax.set_xticklabels(["B: none", "EDCA only", "queue only", "A: both"], fontsize=8); ax.set_title(f"{ln} load", fontsize=9)
        axs[0].set_ylabel("delivery ratio (%)"); axs[0].legend(fontsize=7)
        fig.tight_layout(); fig.savefig(f"{FIG}/fig7_ablation.png"); fig.savefig(f"{FIG}/fig7_ablation.pdf"); plt.close(fig)
    # robustness
    Rb = R[(R.exp == "robust")]
    if len(Rb):
        fig, ax = plt.subplots(figsize=(5.2, 3.2)); keys = [k for k in ("A", "B", "C", "Cup") if k in set(Rb.key)]
        x = np.arange(len(keys)); w = .38
        for off, col, lab in ((-w / 2, "pre_pdr", "before failure"), (w / 2, "post_pdr", "after failure")):
            mu = [ci95(Rb[Rb.key == k][col])[0] * 100 for k in keys]; hh = [ci95(Rb[Rb.key == k][col])[1] * 100 for k in keys]
            ax.bar(x + off, mu, w, yerr=hh, capsize=3, color=[COL[k] for k in keys], alpha=1 if "pre" in col else .5, label=lab)
        ax.set_xticks(x); ax.set_xticklabels(["A\n(10% nodes fail)", "B\n(10% nodes fail)", "C\n(1 of 8 APs fails)", "C-up\n(1 of 8 APs fails)"][:len(keys)], fontsize=7)
        ax.set_ylabel("all-SOS delivery ratio (%)"); ax.legend(fontsize=7); fig.tight_layout()
        fig.savefig(f"{FIG}/fig8_robustness.png"); fig.savefig(f"{FIG}/fig8_robustness.pdf"); plt.close(fig)
    # hop-count distribution of delivered SOS in A/B (G6)
    H = P[(P.key.isin(["A", "B"])) & (P.rx_s >= 0) & (P.hops > 0)]
    if len(H):
        h = H.groupby(["key", "hops"]).size().unstack(0).fillna(0); h = h / h.sum()
        h.to_csv(f"{TAB}/hop_distribution_AB.csv")
    # LaTeX / CSV main table
    M = S[(S.exp == "main") & (S.failure == "none")].copy()
    if len(M):
        def f(r, m, pct=False, d=2):
            v, e = r[m], r[m + "_ci"]; k = 100 if pct else 1
            return "--" if pd.isna(v) else (f"{v*k:.1f}$\\pm${e*k:.1f}" if pd.notna(e) and pct else (f"{v*k:.{d}f}$\\pm${e*k:.{d}f}" if pd.notna(e) else f"{v*k:.{d}f}"))
        rows = []
        for _, r in M.sort_values(["load", "key"], key=lambda c: c.map({**{l: i for i, l in enumerate(LEVELS)}, "A": 0, "B": 1, "C": 2, "Cup": 3}).fillna(9)).iterrows():
            rows.append({"Load": r.load, "Scenario": r.key, "Crit PDR %": f(r, "s3_pdr", True), "Crit <=2s %": f(r, "s3_ok2", True),
                         "Crit p95 delay s": f(r, "s3_dp95"), "High PDR %": f(r, "s2_pdr", True), "All PDR %": f(r, "all_pdr", True),
                         "BG goodput Mbps": f(r, "bg_goodput"), "runs": int(r.runs)})
        Tb = pd.DataFrame(rows); Tb.to_csv(f"{TAB}/main_table.csv", index=False)
        open(f"{TAB}/main_table.tex", "w").write(Tb.to_latex(index=False, escape=False, column_format="llrrrrrrr"))
    print("analysis done:", len(R), "runs;", len(TT), "paired tests; figures in", FIG)

if __name__ == "__main__":
    main()
