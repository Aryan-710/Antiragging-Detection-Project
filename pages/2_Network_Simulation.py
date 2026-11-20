"""
SafeVoice - Network Simulation page (Streamlit multipage; place in <repo>/pages/).
Shows (1) how the app's severity score is mapped to a network priority class and (2) the results of the
ns-3 study in network_simulation/results (figures + tables are produced by tools/analyze.py; nothing here is hard-coded).
"""
import os, sys
import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(os.path.dirname(HERE), "network_simulation")
sys.path.insert(0, os.path.join(SIM, "tools"))
import severity_bridge as sb          # noqa: E402

st.set_page_config(page_title="SafeVoice - Network Simulation", page_icon="📡", layout="wide")
st.title("📡 SafeVoice: delivering alerts when the Wi-Fi cannot")
st.caption("Simulation study (ns-3) of severity-aware alert delivery over a hostel ad-hoc network. "
           "All numbers on this page are read from the study's result files.")

st.header("1. From message severity to network priority")
score = st.slider("Severity score produced by the NLP pipeline", 0.0, 1.0, 0.82, 0.01)
n = sb.to_net_class(score)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Risk level", n.risk_label)
c2.metric("SOS class", n.sos_class)
c3.metric("DSCP / ToS", f"{n.dscp} / 0x{n.tos:02X}")
c4.metric("802.11e access category", n.access_category)
st.markdown("Critical alerts (score >= 0.75) use the voice access category and are dequeued first; when a queue is full, "
            "lower-severity packets are dropped first (push-out).")

st.header("2. Simulation results")
st.info("Headline findings (n=50, 8 runs per cell; see the paper for statistics): provisioned infrastructure Wi-Fi delivered 100% of critical alerts at every load; "
        "severity priority on the ad-hoc mesh did NOT beat the same mesh without priority (neutral up to high load, worse at saturation); "
        "the mesh did beat infrastructure whose shared uplink is congested. The mesh is therefore proposed as a fallback path, not a replacement.")
fig_dir = os.path.join(SIM, "results", "figures"); tab_dir = os.path.join(SIM, "results", "tables")
if not os.path.isdir(fig_dir):
    st.info("Run the study first (see network_simulation/README.md); no result files found.")
else:
    t = os.path.join(tab_dir, "main_table.csv")
    if os.path.exists(t):
        st.subheader("Main comparison (mean +/- 95% CI over independent runs)")
        st.dataframe(pd.read_csv(t), use_container_width=True)
    names = sorted(f for f in os.listdir(fig_dir) if f.endswith(".png"))
    cols = st.columns(2)
    for i, f in enumerate(names):
        cols[i % 2].image(os.path.join(fig_dir, f), caption=f.replace("_", " ")[:-4], use_container_width=True)
    p = os.path.join(tab_dir, "paired_tests.csv")
    if os.path.exists(p):
        with st.expander("Paired Wilcoxon tests (Holm-corrected)"):
            st.dataframe(pd.read_csv(p), use_container_width=True)
st.warning("Simulation results are not field measurements: see the limitations section of the paper "
           "(propagation model, synthetic traffic, ideal orthogonal channels for the Wi-Fi baseline).")
