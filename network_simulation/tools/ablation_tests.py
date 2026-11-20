#!/usr/bin/env python3
"""Paired tests for the ablation, matched on run number (ablation uses runs 1..6; main grid supplies A(both) and B for the same runs)."""
import pandas as pd, numpy as np, sys
from scipy.stats import wilcoxon
R = pd.read_csv("results/tables/per_run_metrics.csv")
rows = []
for load in ["High", "Saturated"]:
    def get(exp, key):
        d = R[(R.exp == exp) & (R.key == key) & (R.load_name == load) & (R.n == 50) & (R.failure == "none")]
        return d.set_index("run")
    B = get("main", "B"); A = get("main", "A"); E = get("ablation", "A-edca"); Q = get("ablation", "A-queue")
    for name, X in [("A-both", A), ("A-edca", E), ("A-queue", Q)]:
        for metric in ["s3_pdr", "s3_ok2", "all_pdr"]:
            runs = sorted(set(X.index) & set(B.index) & set(E.index))   # runs 1..6 common to all four
            d = (X.loc[runs, metric] - B.loc[runs, metric]).values
            if np.all(d == 0): p = np.nan; note = "no difference"
            else: p = wilcoxon(d, zero_method="wilcox", method="exact").pvalue; note = ""
            rows.append(dict(load=load, variant=name, vs="B", metric=metric, n_pairs=len(runs), mean_diff=d.mean(), p=p, note=note))
T = pd.DataFrame(rows)
# Holm within this table (12 hypotheses per metric family over variant x load = 6)
T["p_holm"] = np.nan
for m, g in T.groupby("metric"):
    g = g.dropna(subset=["p"]).sort_values("p"); k = len(g); run = 0
    for i, (ix, r) in enumerate(g.iterrows()):
        run = max(run, min(1, (k - i) * r.p)); T.loc[ix, "p_holm"] = run
T.round(4).to_csv("results/tables/ablation_paired_tests.csv", index=False)
print(T.round(4).to_string())
