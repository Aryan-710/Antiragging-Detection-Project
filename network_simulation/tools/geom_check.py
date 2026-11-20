#!/usr/bin/env python3
"""Geometry-only connectivity/hop-count estimate (mirrors the C++ loss model) used to pick PHY params."""
import sys, numpy as np, collections
sys.path.insert(0, "tools")
L, W, FH = 40.0, 15.0, 3.0
RW = L / 6
def walls(p, q):
    (x1, y1), (x2, y2) = p[:2], q[:2]
    c = 0
    for yw in (6.0, 9.0):
        if (y1 - yw) * (y2 - yw) < 0: c += 1
    if abs(x2 - x1) > 1e-9:
        for k in range(1, 6):
            xw = k * RW
            if (x1 - xw) * (x2 - xw) < 0:
                y = y1 + (y2 - y1) * (xw - x1) / (x2 - x1)
                if y < 6.0 or y > 9.0: c += 1
    return c
def loss(p, q, n=3.0, ref=46.6777, wl=5.0, fl=13.0):
    d = max(np.linalg.norm(np.array(p) - np.array(q)), 1.0)
    fc = abs(int(p[2] // FH) - int(q[2] // FH))
    return ref + 10 * n * np.log10(d) + wl * walls(p, q) + fl * fc
def load_nodes(fn):
    return [tuple(map(float, l.split()[2:5])) for l in open(fn) if l.startswith("NODE")]
def hops(nodes, tx, thr, **kw):
    N = len(nodes); desk = N - 1
    adj = [[j for j in range(N) if j != i and tx - loss(nodes[i], nodes[j], **kw) >= thr] for i in range(N)]
    dist = {desk: 0}; q = [desk]
    for u in q:
        for v in adj[u]:
            if v not in dist: dist[v] = dist[u] + 1; q.append(v)
    return dist, adj
if __name__ == "__main__":
    nodes = load_nodes(sys.argv[1])
    for tx in (10, 13, 16, 20):
        for thr in (-78,):
            dist, adj = hops(nodes, tx, thr)
            h = [dist[i] for i in range(len(nodes) - 1) if i in dist]
            unreach = len(nodes) - 1 - len(h)
            deg = np.mean([len(a) for a in adj])
            print(f"tx={tx} dBm thr={thr}: reachable {len(h)}/{len(nodes)-1} hops={dict(sorted(collections.Counter(h).items()))} mean_deg={deg:.1f}")
