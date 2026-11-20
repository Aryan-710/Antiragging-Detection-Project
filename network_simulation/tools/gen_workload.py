#!/usr/bin/env python3
"""
SafeVoice workload generator (scenario-independent).

Writes ONE text file that fully specifies a simulation input: node positions,
mobility traces, SOS arrivals (with severity), warm-up pings, background flows
and the failure sets.  Scenarios A, B and C all read the SAME file, so they see
identical positions, mobility, traffic and failures.  All randomness lives
here, in dedicated numpy streams seeded from (BASE_SEED, run, nNodes[, load]);
the ns-3 side of the simulator draws no random numbers for the workload.

Hostel geometry (shared with the C++ propagation model, keep in sync):
  4 floors, 40 m x 15 m, floor height 3 m, central corridor y in [6, 9],
  6 rooms per side of the corridor -> 12 rooms/floor, 48 rooms in total.
"""
import argparse, hashlib, sys
import numpy as np

BASE_SEED = 20261120
L, W, FLOORS, FH = 40.0, 15.0, 4, 3.0
CORR_Y0, CORR_Y1 = 6.0, 9.0
ROOMS_PER_SIDE = 6
Z_OFF = 1.2                      # device height above floor
DESK = (2.0, 7.5, Z_OFF)         # security desk: ground-floor corridor, near entrance

def fnv1a64(data: bytes) -> int:
    h = 0xcbf29ce484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h

def rng(run, n, comp, extra=0):
    return np.random.default_rng(np.random.SeedSequence([BASE_SEED, run, n, comp, extra]))

def place_static(r, floor):
    side = r.integers(0, 2)
    col = r.integers(0, ROOMS_PER_SIDE)
    rw = L / ROOMS_PER_SIDE
    x = col * rw + r.uniform(0.5, rw - 0.5)
    y = (r.uniform(0.5, CORR_Y0 - 0.5) if side == 0 else r.uniform(CORR_Y1 + 0.5, W - 0.5))
    return x, y, floor * FH + Z_OFF

def gen(run, n, load_mbps, duration, warmup, sos_rate, burst_t, mobile_frac,
        fail_frac, p_gw, video_share, sos_tail, vmin=0.25, vmax=0.5, web_on=0.5):
    lines = []
    end_t = warmup + duration
    # ---------------- nodes + mobility (stream 1) ----------------
    r = rng(run, n, 1)
    mobile_ids = set(r.choice(n, size=int(round(mobile_frac * n)), replace=False).tolist())
    nodes, wpts = [], []
    for i in range(n):
        floor = int(r.integers(0, FLOORS))
        if i in mobile_ids:
            x, y = r.uniform(0, L), r.uniform(CORR_Y0 + 0.5, CORR_Y1 - 0.5)
            z = floor * FH + Z_OFF
            nodes.append((i, x, y, z, 1))
            # random-waypoint inside the corridor: speed U(1,1.5) m/s, pause U(0,10) s
            t, cx, cy = 0.0, x, y
            wpts.append((i, 0.0, cx, cy, z))
            while t < end_t + 30:
                nx, ny = r.uniform(0, L), r.uniform(CORR_Y0 + 0.5, CORR_Y1 - 0.5)
                v = r.uniform(1.0, 1.5)
                dist = float(np.hypot(nx - cx, ny - cy))
                t += max(dist / v, 1e-3)
                wpts.append((i, round(t, 6), nx, ny, z))
                cx, cy = nx, ny
                pause = r.uniform(0, 10)
                if pause > 0:
                    t += pause
                    wpts.append((i, round(t, 6), cx, cy, z))
        else:
            x, y, z = place_static(r, floor)
            nodes.append((i, x, y, z, 0))
    # desk = node n
    nodes.append((n, *DESK, 0))
    # ---------------- SOS (stream 2) ----------------
    r = rng(run, n, 2)
    sos = []
    t = warmup
    while True:
        t += r.exponential(1.0 / sos_rate)
        if t >= end_t - sos_tail:
            break
        sev = int(r.choice([3, 2, 1], p=[0.1, 0.3, 0.6]))
        sos.append((round(t, 6), int(r.integers(0, n)), sev, 0))
    # burst: 20 SOS from distinct nodes within 5 s, forced mix 5 critical / 7 high / 8 low
    bsrc = r.choice(n, size=20, replace=False)
    bsev = [3] * 5 + [2] * 7 + [1] * 8
    r.shuffle(bsev)
    for s, v in zip(bsrc, bsev):
        sos.append((round(burst_t + r.uniform(0, 5.0), 6), int(s), int(v), 1))
    sos.sort()
    # ---------------- warm-up pings (stream 3) ----------------
    r = rng(run, n, 3)
    warm = [(round(r.uniform(2.0, warmup - 2.0), 6), i) for i in range(n)]
    warm.sort()
    # ---------------- background flows (stream 4, depends on load) ----------------
    r = rng(run, n, 4, int(round(load_mbps * 1000)))
    bg = []   # (flow, src, dst, size, rate_bps, t0, t1, kind)
    fid = 0
    def pick_pair():
        s = int(r.integers(0, n))
        if r.random() < p_gw:
            return s, n
        d = int(r.integers(0, n - 1))
        return s, d + (d >= s)
    # video-like CBR: rate U(vmin,vmax) Mbps (default 0.25-0.5), 1200 B, whole run
    remaining = load_mbps * video_share
    while remaining > 1e-9:
        rate = min(float(r.uniform(vmin, vmax)), remaining)
        remaining -= rate
        s, d = pick_pair()
        t0 = float(r.uniform(1.0, warmup - 1.0))
        bg.append((fid, s, d, 1200, int(rate * 1e6), round(t0, 6), end_t, 0)); fid += 1
    # web-like on/off: web_on Mbps while ON; ON~Exp(1 s), OFF~Exp(3 s) (duty 25%)
    remaining = load_mbps * (1 - video_share)
    while remaining > 1e-9:
        mean_rate = min(0.25 * web_on, remaining)
        remaining -= mean_rate
        on_rate = mean_rate / 0.25
        s, d = pick_pair()
        t = float(r.uniform(1.0, warmup - 1.0))
        while t < end_t:
            on = r.exponential(1.0)
            t1 = min(t + on, end_t)
            if t1 - t > 0.02:
                bg.append((fid, s, d, 1000, int(on_rate * 1e6), round(t, 6), round(t1, 6), 1))
            t = t1 + r.exponential(3.0)
        fid += 1
    # ---------------- failures (stream 5) ----------------
    r = rng(run, n, 5)
    failn = sorted(r.choice(n, size=max(1, int(round(fail_frac * n))), replace=False).tolist())
    failap = int(r.integers(0, 2 * FLOORS))   # index into the 8 APs of scenario C

    out = ["SVWL 1"]
    out.append(f"PARAM run {run}"); out.append(f"PARAM n {n}")
    out.append(f"PARAM load_mbps {load_mbps}"); out.append(f"PARAM duration {duration}")
    out.append(f"PARAM warmup {warmup}"); out.append(f"PARAM sos_rate {sos_rate}")
    out.append(f"PARAM burst_t {burst_t}")
    out += [f"NODE {i} {x:.4f} {y:.4f} {z:.4f} {m}" for i, x, y, z, m in nodes]
    out += [f"WPT {i} {t} {x:.4f} {y:.4f} {z:.4f}" for i, t, x, y, z in wpts]
    out += [f"SOS {t} {s} {v} {b}" for t, s, v, b in sos]
    out += [f"WARM {t} {s}" for t, s in warm]
    out += [f"BG {f} {s} {d} {sz} {rt} {a} {b} {k}" for f, s, d, sz, rt, a, b, k in bg]
    out += [f"FAILNODE {i}" for i in failn]
    out.append(f"FAILAP {failap}")
    return "\n".join(out) + "\n", dict(n_sos=len(sos), n_bg_lines=len(bg),
                                       offered_bg_mbps=sum(x[4] * (x[6] - x[5]) for x in bg) / (end_t - warmup) / 1e6)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--load", type=float, default=0.0, help="offered background load, Mbps (aggregate)")
    ap.add_argument("--duration", type=float, default=200.0)
    ap.add_argument("--warmup", type=float, default=10.0)
    ap.add_argument("--sos-rate", type=float, default=3.0)
    ap.add_argument("--burst-t", type=float, default=60.0)
    ap.add_argument("--mobile-frac", type=float, default=0.2)
    ap.add_argument("--fail-frac", type=float, default=0.1)
    ap.add_argument("--p-gw", type=float, default=0.3)
    ap.add_argument("--video-share", type=float, default=0.7)
    ap.add_argument("--sos-tail", type=float, default=5.0, help="no SOS generated in the last N s")
    ap.add_argument("--video-min", type=float, default=0.25, help="Mbps")
    ap.add_argument("--video-max", type=float, default=0.5, help="Mbps")
    ap.add_argument("--web-on", type=float, default=0.5, help="Mbps while ON")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    txt, info = gen(a.run, a.n, a.load, a.duration, a.warmup, a.sos_rate, a.burst_t,
                    a.mobile_frac, a.fail_frac, a.p_gw, a.video_share, a.sos_tail,
                    a.video_min, a.video_max, a.web_on)
    data = txt.encode()
    with open(a.out, "wb") as f:
        f.write(data)
    print(f"{a.out} fnv1a64={fnv1a64(data):016x} sos={info['n_sos']} bg_lines={info['n_bg_lines']} "
          f"offered_bg={info['offered_bg_mbps']:.3f}Mbps")

if __name__ == "__main__":
    main()
