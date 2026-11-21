"""Streamlit helper: live ns-3 simulation + animated demo for CRITICAL reports in the SafeVoice app."""
import json, os, sys
import streamlit as st
import streamlit.components.v1 as components

_HERE = os.path.dirname(os.path.abspath(__file__))
_HTML = os.path.join(_HERE, "demo", "alert_demo.html")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


@st.cache_resource(show_spinner=False)
def _live_available():
    try:
        import live_sim
        return bool(live_sim.available())
    except Exception:
        return False


def live_controls():
    """Call BEFORE the Analyze button (changing a widget reruns the page, which would clear a shown report).
    Returns (live_enabled, load_name)."""
    ok = _live_available()
    with st.sidebar:
        st.markdown("### 📡 Network simulation")
        if ok:
            on = st.checkbox("Run LIVE ns-3 simulation on CRITICAL alerts", value=True,
                             help="Runs the real ns-3 simulator for the alert (about 15-40 s).")
            load = st.selectbox("Hostel network load", ["Low", "Medium", "High", "Saturated"], index=1,
                                help="Background traffic as a fraction of mesh capacity: 10/40/80/120 %.")
            return on, load
        st.caption("ns-3 simulator not found on this machine: CRITICAL alerts show the replay of recorded study results. "
                   "See network_simulation/demo/DEMO_GUIDE.md to enable live mode.")
    return False, "Medium"


def render_alert_demo(severity_score=None, live_enabled=False, load="Medium", height=1100):
    st.markdown("---")
    st.subheader("📡 How the critical alert reaches the security desk (ns-3 network simulation)")
    live = None
    if live_enabled:
        try:
            import live_sim
            with st.spinner("Running the real ns-3 simulator for this alert in 4 systems (about 15-40 s)..."):
                live = live_sim.run_live(load)
            cols = st.columns(4)
            names = {"A": "Mesh + priority", "B": "Mesh, no priority", "C": "Provisioned Wi-Fi", "Cup": "Wi-Fi, congested uplink"}
            for c, k in zip(cols, ["A", "B", "C", "Cup"]):
                r = live["systems"][k]
                c.metric(names[k], "✔ delivered" if r["delivered"] else "✖ LOST",
                         (f"{r['delay_ms']:.1f} ms, {r['hops']} hop(s)" if r["delivered"] else "not received"), delta_color="off")
                c.caption(f"all critical in window: {r['crit_ok']}/{r['crit_sent']}")
            st.caption(f"Live run: seed {live['seed']}, load {load}, {live['window']:.0f} s window after warm-up, wall time {live['wall_s']} s. "
                       "One seed = one sample, not a statistic; the statistics are in the 208-run study.")
        except Exception as e:
            st.warning(f"Live simulation failed ({e}); showing recorded results instead.")
            live = None
    with open(_HTML, encoding="utf-8") as f:
        page = f.read()
    score = "null" if severity_score is None else "%.4f" % float(severity_score)
    page = (page.replace("/*__SCORE__*/null", score).replace("/*__AUTOSTART__*/false", "true")
            .replace("/*__LIVE__*/null", json.dumps(live) if live else "null"))
    components.html(page, height=height, scrolling=True)
