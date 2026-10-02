"""Prioritized planning with Space-Time A*: the multi-agent plan generator."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .grid import Cell, Grid
from .reservation import Blockages, ReservationTable
from .st_astar import st_astar


@dataclass
class PlanRequest:
    aid: int
    start: Cell
    t0: int
    goals: list[Cell]
    prefix: list[Cell]      # path[:t0] that is already fixed (absolute time)


def plan_prioritized(
    grid: Grid,
    rt: ReservationTable,
    blockages: Blockages | None,
    requests: list[PlanRequest],
    rng: np.random.Generator | None = None,
    fixed_head: int = 0,
    attempts: int = 8,
    park_unplanned: bool = True,
    max_expansions: int = 300_000,
) -> dict[int, list[Cell]] | None:
    """Plan every request one by one, each against the reservations of those before it.

    With `park_unplanned`, agents not yet planned are treated as parked on their
    start cell so that higher-priority agents do not plan through them (safe when
    agents start on docks). Otherwise only their current cell at t0 is reserved
    and they must get out of the way of higher-priority agents. The first `fixed_head`
    requests keep their position in the order across restarts (e.g. emergency
    agents); the rest are shuffled if an attempt fails.

    On success the new paths are left in `rt` and returned as full absolute-time
    paths. On failure `rt` is restored and None is returned.
    """
    order = list(requests)
    for attempt in range(attempts):
        if attempt > 0 and rng is not None:
            tail = order[fixed_head:]
            rng.shuffle(tail)
            order = order[:fixed_head] + tail
        if park_unplanned:
            for r in order:
                rt.park(r.aid, r.start, r.t0)
        planned: dict[int, list[Cell]] = {}
        ok = True
        for r in order:
            rt.unpark(r.aid, r.start)
            suffix = st_astar(grid, r.start, r.t0, r.goals, r.aid, rt, blockages, max_expansions)
            if suffix is None:
                ok = False
                break
            full = r.prefix + suffix
            rt.add_path(r.aid, full, r.t0)
            planned[r.aid] = full
        if ok:
            return planned
        for aid, full in planned.items():
            rt.remove_path(aid, full, next(q.t0 for q in order if q.aid == aid))
        for r in order:
            rt.unpark(r.aid, r.start)
        if rng is None:
            break
    return None
