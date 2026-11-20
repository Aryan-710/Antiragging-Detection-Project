#!/usr/bin/env python3
"""Emit LaTeX tables for the paper directly from results/tables/*.csv (no hand-typed numbers)."""
import pandas as pd, numpy as np, shutil, glob, os
T="results/tables"; O="paper/tables"
s=pd.read_csv(f"{T}/summary_mean_ci.csv")
def pm(v,c,f="{:.1f}",sc=100):
    return (f+"$\\pm$"+f).format(v*sc,c*sc) if pd.notna(c) else f.format(v*sc)
names={"A":"A","B":"B","C":"C","Cup":"C-up"}
order=["Low","Medium","High","Saturated"]
# main table
L=["\\begin{tabular}{@{}llrrrrr@{}}\\toprule","Load & Sys & crit.\\ PDR (\\%) & crit.\\ $\\le$2\\,s (\\%) & crit.\\ p95 (s) & burst crit.\\ PDR (\\%) & bg goodput (Mb/s)\\\\\\midrule"]
m=s[s.exp=="main"]
for ld in order:
    for k in ["A","B","C","Cup"]:
        r=m[(m.load==ld)&(m.key==k)].iloc[0]
        L.append(f"{ld if k=='A' else ''} & {names[k]} & {pm(r.s3_pdr,r.s3_pdr_ci)} & {pm(r.s3_ok2,r.s3_ok2_ci)} & {r.s3_dp95:.2f} & {r.burstcrit_pdr*100:.0f} & {r.bg_goodput:.2f}\\\\")
    if ld!="Saturated": L.append("\\midrule")
L.append("\\bottomrule\\end{tabular}")
open(f"{O}/main_tab.tex","w").write("\n".join(L))
# ablation (runs 1-6 only via ablation exp) + main A,B for reference
L=["\\begin{tabular}{@{}llrrrr@{}}\\toprule","Load & Variant & crit.\\ PDR (\\%) & crit.\\ $\\le$2\\,s (\\%) & all-SOS PDR (\\%) & push-outs/run\\\\\\midrule"]
for ld in ["High","Saturated"]:
    for key,lab,exp in [("B","B (none)","main"),("A-edca","EDCA only","ablation"),("A-queue","queue only","ablation"),("A","both (A)","main")]:
        r=s[(s.exp==exp)&(s.key==key)&(s.load==ld)&(s.n==50)].iloc[0]
        L.append(f"{ld if key=='B' else ''} & {lab} & {pm(r.s3_pdr,r.s3_pdr_ci)} & {pm(r.s3_ok2,r.s3_ok2_ci)} & {pm(r.all_pdr,r.all_pdr_ci)} & {r.tcPushout:.1f}\\\\")
    if ld=="High": L.append("\\midrule")
L.append("\\bottomrule\\end{tabular}")
open(f"{O}/ablation_tab.tex","w").write("\n".join(L))
# scale / density / robust
L=["\\begin{tabular}{@{}lllrrrr@{}}\\toprule","Exp. & $n$ / fault & Sys & runs & crit.\\ PDR (\\%) & all-SOS PDR (\\%) & ctrl pkts/s\\\\\\midrule"]
def row(r,lab,exp):
    return f"{exp} & {lab} & {names[r.key]} & {int(r.runs)} & {pm(r.s3_pdr,r.s3_pdr_ci)} & {pm(r.all_pdr,r.all_pdr_ci)} & {r.ctrl_pps:.0f}\\\\"
for n in [25,50,75,100]:
    for k in ["A","B","C","Cup"]:
        if n==50: r=m[(m.load=="High")&(m.key==k)].iloc[0]
        else:
            q=s[(s.n==n)&(s.key==k)&(s.exp.isin(["density","confirm100"]))]
            r=q.iloc[0]
        L.append(row(r,f"n={n}","main" if n==50 else ("confirm" if n==100 else "density")))
    L.append("\\midrule")
for k in ["A","B","C","Cup"]:
    q=s[(s.exp=="robust")&(s.key==k)]
    r=q.iloc[0]; L.append(row(r,"10\\% nodes" if k in("A","B") else "1 AP","robust"))
L.append("\\bottomrule\\end{tabular}")
open(f"{O}/scale_tab.tex","w").write("\n".join(L))
# calibration
c=pd.read_csv("results/calib/n50/calibration.csv")
L=["\\begin{tabular}{@{}rrrr@{}}\\toprule","offered (Mb/s) & carried (Mb/s) & bg goodput (Mb/s) & bg PDR (\\%)\\\\\\midrule"]
for _,r in c.iterrows(): L.append(f"{r.offered_Mbps:.0f} & {r.tx_Mbps:.2f} & {r.goodput_Mbps:.2f} & {r.bg_pdr*100:.1f}\\\\")
L.append("\\bottomrule\\end{tabular}")
open(f"{O}/calibration_tab.tex","w").write("\n".join(L))
# paired tests (main, vs B/C, crit PDR)
t=pd.read_csv(f"{T}/paired_tests.csv"); t=t[(t.exp=="main")&(t.metric=="s3_pdr")]
L=["\\begin{tabular}{@{}llrrrrr@{}}\\toprule","Load & A vs & mean diff (pp) & $p$ & $p_{\\rm Holm}$ & rank-biserial $r$ & pairs\\\\\\midrule"]
for ld in order:
    for b in ["B","C","Cup"]:
        r=t[(t.load==ld)&(t.B==b)].iloc[0]
        L.append(f"{ld if b=='B' else ''} & {names[b]} & {r.mdiff*100:+.1f} & {r.p:.4f} & {r.p_holm:.3f} & {r.r:+.2f} & {int(r.n_pairs)}\\\\")
L.append("\\bottomrule\\end{tabular}")
open(f"{O}/paired_tab.tex","w").write("\n".join(L))
for f in glob.glob("results/figures/*.pdf"): shutil.copy(f,"paper/figures/")
print("ok")
