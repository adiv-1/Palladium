"""Minimum-cost assignment (Hungarian / Kuhn-Munkres with potentials, O(n^2 m)).

Cost matrices here are tiny (tracks x detections in one camera view), so a short pure-Python
solver is fast enough and saves a SciPy dependency.
"""
from __future__ import annotations

import numpy as np

_INFEASIBLE = 1e6


def linear_assignment(
    cost: np.ndarray, max_cost: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Return (matches, unmatched_rows, unmatched_cols); pairs costing more than ``max_cost``
    are never matched."""
    n_rows, n_cols = cost.shape if cost.ndim == 2 else (0, 0)
    if n_rows == 0 or n_cols == 0:
        return [], list(range(n_rows)), list(range(n_cols))

    gated = np.where(cost > max_cost, _INFEASIBLE, cost)
    transposed = n_rows > n_cols
    a = (gated.T if transposed else gated).tolist()
    n, m = len(a), len(a[0])

    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)      # p[j] = row (1-based) assigned to column j
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            row = a[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    matches: list[tuple[int, int]] = []
    for j in range(1, m + 1):
        if p[j] == 0:
            continue
        r, c = (j - 1, p[j] - 1) if transposed else (p[j] - 1, j - 1)
        if cost[r, c] <= max_cost:
            matches.append((r, c))
    matched_rows = {r for r, _ in matches}
    matched_cols = {c for _, c in matches}
    return (
        matches,
        [r for r in range(n_rows) if r not in matched_rows],
        [c for c in range(n_cols) if c not in matched_cols],
    )
