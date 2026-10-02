"""Multi-goal Space-Time A*.

State = (cell, goal_index, t). Goals must be visited in order; the last goal is
where the agent parks, so it is only accepted once nobody else needs that cell
later. The heuristic is the exact static distance through the remaining goals.

Once t passes the last time at which anything in the environment changes
(`t_static`), the problem is time-invariant, so states are deduplicated on
min(t, t_static + 1). This keeps the search finite even if the goal is
unreachable.
"""
from __future__ import annotations

import heapq
from itertools import count

from .grid import Cell, Grid, INF
from .reservation import Blockages, ReservationTable


def st_astar(
    grid: Grid,
    start: Cell,
    t0: int,
    goals: list[Cell],
    aid: int,
    rt: ReservationTable | None = None,
    blockages: Blockages | None = None,
    max_expansions: int = 300_000,
) -> list[Cell] | None:
    """Return the cells occupied at times t0, t0+1, ..., arrival (inclusive), or None."""
    n = len(goals)
    dlists = [grid.dist_list(g) for g in goals]
    suffix = [0] * n
    for j in range(n - 2, -1, -1):
        suffix[j] = suffix[j + 1] + dlists[j + 1][goals[j][0]][goals[j][1]]

    def h(c: Cell, gi: int) -> int:
        return dlists[gi][c[0]][c[1]] + suffix[gi]

    def advance(c: Cell, gi: int) -> int:
        while gi < n - 1 and c == goals[gi]:
            gi += 1
        return gi

    def can_finish(c: Cell, t: int) -> bool:
        return (c == goals[-1]
                and (rt is None or rt.can_park(c, t, aid))
                and (blockages is None or blockages.can_park(c, t)))

    # Earliest time the agent may park on its final goal: after every other
    # reservation of that cell and after any known blockage of it ends. This
    # lower-bounds the arrival time and steers the search when the goal cell is
    # still in use by others (otherwise A* floods a large plateau of states).
    g_last = goals[-1]
    t_park = t0
    if rt is not None:
        p = rt.parked.get(g_last)
        if p is not None and p[0] != aid:
            return None
        for tt, owner in rt.cell_res.get(g_last, {}).items():
            if owner != aid and tt > t_park:
                t_park = tt
    if blockages is not None:
        for _, e in blockages.intervals.get(g_last, ()):
            if e >= INF:
                return None
            t_park = max(t_park, int(e))

    # Fast infeasibility test: cells blocked forever right now (agents parked
    # there already, permanent blockages) must not cut the start off from a goal.
    if rt is not None or blockages is not None:
        walls = set()
        if rt is not None:
            walls |= {c for c, (o, pt) in rt.parked.items() if o != aid and pt <= t0}
        if blockages is not None:
            walls |= blockages.permanent_cells()
        if walls:
            reach = grid.reachable(start, walls - {start})
            if any(g not in reach for g in goals):
                return None

    gi0 = advance(start, 0)
    h0 = max(h(start, gi0), t_park - t0)
    if h0 >= INF:
        return None

    t_static = t0
    if rt is not None:
        t_static = max(t_static, rt.max_time)
    if blockages is not None:
        t_static = max(t_static, blockages.max_known_time)

    # Local bindings for speed (this loop dominates the run time).
    nbrs = grid._nbrs
    cell_res = rt.cell_res if rt is not None else {}
    edge_res = rt.edge_res if rt is not None else {}
    parked = rt.parked if rt is not None else {}
    blk_iv = blockages.intervals if blockages is not None else {}
    heappush, heappop = heapq.heappush, heapq.heappop
    t_cap = t_static + 1

    # node = (cell, gi, t, parent_index)
    nodes: list[tuple[Cell, int, int, int]] = [(start, gi0, t0, -1)]
    tie = count()
    open_ = [(t0 + h0, -t0, next(tie), 0)]
    closed: set = set()
    expansions = 0

    while open_:
        _, _, _, idx = heappop(open_)
        c, gi, t, _ = nodes[idx]
        key = (c, gi, t if t < t_cap else t_cap)
        if key in closed:
            continue
        closed.add(key)
        if gi == n - 1 and can_finish(c, t):
            path = []
            while idx != -1:
                path.append(nodes[idx][0])
                idx = nodes[idx][3]
            return path[::-1]
        expansions += 1
        if expansions > max_expansions:
            return None
        nt = t + 1
        for nc in (c, *nbrs[c]):
            iv = blk_iv.get(nc)
            if iv and any(s <= nt < e for s, e in iv):
                continue
            res = cell_res.get(nc)
            if res:
                owner = res.get(nt)
                if owner is not None and owner != aid:
                    continue
            p = parked.get(nc)
            if p is not None and p[0] != aid and nt >= p[1]:
                continue
            if nc != c:
                owner = edge_res.get((nc, c, t))
                if owner is not None and owner != aid:
                    continue
            ngi = advance(nc, gi)
            hv = h(nc, ngi)
            if hv >= INF:
                continue
            if t_park - nt > hv:
                hv = t_park - nt
            if (nc, ngi, nt if nt < t_cap else t_cap) in closed:
                continue
            nodes.append((nc, ngi, nt, idx))
            heappush(open_, (nt + hv, -nt, next(tie), len(nodes) - 1))
    return None
