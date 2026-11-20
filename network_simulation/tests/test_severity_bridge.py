import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import pytest
import severity_bridge as sb

APP = os.path.join(os.path.dirname(__file__), "..", "..", "safecampus_app.py")   # repo root when placed in network_simulation/

def test_thresholds_match_app():
    if not os.path.exists(APP):
        pytest.skip("app file not next to the study (run from inside the repo)")
    src = open(APP).read()
    for key, val in (("threshold_low", sb.T_LOW), ("threshold_high", sb.T_HIGH), ("threshold_critical", sb.T_CRIT)):
        m = re.search(rf'"{key}":\s*([0-9.]+)', src)
        assert m and float(m.group(1)) == val, key

@pytest.mark.parametrize("score,cls,ac", [(0.0, 1, "AC_BE"), (0.249, 1, "AC_BE"), (0.25, 1, "AC_BE"), (0.499, 1, "AC_BE"),
                                          (0.50, 2, "AC_VI"), (0.749, 2, "AC_VI"), (0.75, 3, "AC_VO"), (1.0, 3, "AC_VO")])
def test_mapping(score, cls, ac):
    n = sb.to_net_class(score)
    assert (n.sos_class, n.access_category) == (cls, ac)

def test_dscp_to_ac_matches_simulator():
    # simulator: ToS 0xC0 -> UP6 -> AC_VO ; 0xA0 -> UP5 -> AC_VI ; 0 -> UP0 -> AC_BE   (UP = ToS >> 5)
    up_to_ac = {0: "AC_BE", 3: "AC_BE", 1: "AC_BK", 2: "AC_BK", 4: "AC_VI", 5: "AC_VI", 6: "AC_VO", 7: "AC_VO"}
    for s in (0.1, 0.6, 0.9):
        n = sb.to_net_class(s)
        assert up_to_ac[n.tos >> 5] == n.access_category and n.dscp == n.tos >> 2

def test_range_check():
    with pytest.raises(ValueError):
        sb.to_net_class(1.2)
