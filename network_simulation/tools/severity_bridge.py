"""
Bridge between the SafeVoice NLP app (severity score 0..1) and the network layer used in the ns-3 study.

The app (safecampus_app.py, get_risk_info) maps the composite severity score to four risk levels with the
thresholds in CONFIG: threshold_low=0.25, threshold_high=0.50, threshold_critical=0.75.  The network study uses
three SOS classes.  This module is the single place that defines that mapping, so the app and the simulation
cannot silently drift apart (tests/test_severity_bridge.py parses the app's CONFIG and checks the thresholds).

  risk level   score        SOS class   IP DSCP / ToS   802.11e AC   UDP dest port (queue disc)
  CRITICAL     >= 0.75      3           CS6 / 0xC0      AC_VO        7003
  HIGH         >= 0.50      2           CS5 / 0xA0      AC_VI        7002
  MODERATE/LOW < 0.50       1           0   / 0x00      AC_BE        7001
"""
from dataclasses import dataclass

T_LOW, T_HIGH, T_CRIT = 0.25, 0.50, 0.75     # must equal safecampus_app.CONFIG (checked by the test)

@dataclass(frozen=True)
class NetClass:
    sos_class: int
    risk_label: str
    dscp: int
    tos: int
    access_category: str
    udp_port: int

def risk_label(score: float) -> str:
    if score >= T_CRIT: return "CRITICAL"
    if score >= T_HIGH: return "HIGH"
    if score >= T_LOW: return "MODERATE"
    return "LOW"

def to_net_class(score: float) -> NetClass:
    """Map an app severity score in [0,1] to the network class used by the simulator (scenario A)."""
    if not 0.0 <= score <= 1.0:
        raise ValueError("severity score must be within [0, 1]")
    lab = risk_label(score)
    if lab == "CRITICAL": return NetClass(3, lab, 48, 0xC0, "AC_VO", 7003)
    if lab == "HIGH":     return NetClass(2, lab, 40, 0xA0, "AC_VI", 7002)
    return NetClass(1, lab, 0, 0x00, "AC_BE", 7001)
