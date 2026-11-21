"""Streamlit helper: show the animated 'what happens to a CRITICAL alert' demo inside the SafeVoice app."""
import os
import streamlit as st
import streamlit.components.v1 as components

_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "alert_demo.html")


def render_alert_demo(severity_score=None, autostart=True, height=1050):
    """Embed the self-contained demo page. severity_score (0..1) is shown in the banner; autostart sends one alert on load."""
    with open(_HTML, encoding="utf-8") as f:
        page = f.read()
    score = "null" if severity_score is None else "%.4f" % float(severity_score)
    page = page.replace("/*__SCORE__*/null", score).replace("/*__AUTOSTART__*/false", "true" if autostart else "false")
    st.markdown("---")
    st.subheader("📡 How the critical alert reaches the security desk (network simulation)")
    components.html(page, height=height, scrolling=True)
