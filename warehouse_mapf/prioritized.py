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
    prefix: list[Cell]


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
