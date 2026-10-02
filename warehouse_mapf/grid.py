"""Grid map and warehouse layout generator.

Cells are (row, col) tuples. Movement is 4-connected; waiting is always allowed.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

Cell = tuple[int, int]
INF = 10**9
MOVES = ((-1, 0), (1, 0), (0, -1), (0, 1))


class Grid:
    def __init__(self, obstacles: np.ndarray, dead_ends: set[Cell] | None = None):
        """`dead_ends` are cells (e.g. robot docks) that connect only vertically, so
        robots never drive through a row of docks to get somewhere else."""
        self.obs = np.asarray(obstacles, dtype=bool)
        dead_ends = dead_ends or set()
        self.h, self.w = self.obs.shape
        self._dist_cache: dict[Cell, np.ndarray] = {}
        self._list_cache: dict[Cell, list[list[int]]] = {}
        self._nbrs: dict[Cell, list[Cell]] = {}
        for r in range(self.h):
            for c in range(self.w):
                if not self.obs[r, c]:
                    self._nbrs[(r, c)] = [
                        (r + dr, c + dc)
                        for dr, dc in MOVES
                        if 0 <= r + dr < self.h and 0 <= c + dc < self.w and not self.obs[r + dr, c + dc]
                        and not (dc != 0 and ((r, c) in dead_ends or (r + dr, c + dc) in dead_ends))
                    ]

    def passable(self, cell: Cell) -> bool:
        r, c = cell
        return 0 <= r < self.h and 0 <= c < self.w and not self.obs[r, c]

    def neighbors(self, cell: Cell) -> list[Cell]:
        return self._nbrs[cell]

    def free_cells(self) -> list[Cell]:
        return list(self._nbrs.keys())

    def dist_map(self, goal: Cell) -> np.ndarray:
        """True shortest-path distance from every cell to `goal` on the static map (BFS, cached)."""
        d = self._dist_cache.get(goal)
        if d is None:
            d = np.full((self.h, self.w), INF, dtype=np.int64)
            d[goal] = 0
            q = deque([goal])
            while q:
                cur = q.popleft()
                nd = d[cur] + 1
                for n in self._nbrs[cur]:
                    if d[n] > nd:
                        d[n] = nd
                        q.append(n)
            self._dist_cache[goal] = d
        return d

    def dist_list(self, goal: Cell) -> list[list[int]]:
        """Same as dist_map but as nested Python lists (faster to index in hot loops)."""
        d = self._list_cache.get(goal)
        if d is None:
            d = self._list_cache[goal] = self.dist_map(goal).tolist()
        return d

    def dist(self, a: Cell, b: Cell) -> int:
        return int(self.dist_map(b)[a])

    def reachable(self, src: Cell, blocked: set[Cell]) -> set[Cell]:
        """Cells reachable from `src` when `blocked` cells are treated as extra obstacles."""
        if src in blocked:
            return set()
        seen = {src}
        q = deque([src])
        while q:
            cur = q.popleft()
            for n in self._nbrs[cur]:
                if n not in seen and n not in blocked:
                    seen.add(n)
                    q.append(n)
        return seen


@dataclass
class Warehouse:
    grid: Grid
    shelves: list[Cell]
    pickups: list[Cell]      # aisle cells adjacent to a shelf (where items are picked)
    stations: list[Cell]     # delivery / packing stations on the left & right walls
    homes: list[Cell]        # robot docks on the top & bottom walls
    meta: dict = field(default_factory=dict)


def make_warehouse(
    block_rows: int = 5,
    block_cols: int = 5,
    block_h: int = 2,
    block_w: int = 5,
    aisle: int = 1,
    margin: int = 2,
) -> Warehouse:
    """Kiva-style layout: a grid of shelf blocks separated by aisles, with a free
    perimeter corridor. Outermost rows hold robot docks (homes); outermost columns
    hold delivery stations. Docks are dead-end bays entered only from the corridor
    cell in front of them."""
    h = 2 * margin + block_rows * block_h + (block_rows - 1) * aisle
    w = 2 * margin + block_cols * block_w + (block_cols - 1) * aisle
    obs = np.zeros((h, w), dtype=bool)
    shelves = []
    for br in range(block_rows):
        r0 = margin + br * (block_h + aisle)
        for bc in range(block_cols):
            c0 = margin + bc * (block_w + aisle)
            obs[r0:r0 + block_h, c0:c0 + block_w] = True
            shelves += [(r, c) for r in range(r0, r0 + block_h) for c in range(c0, c0 + block_w)]
    stations = [(r, c) for c in (0, w - 1) for r in range(margin, h - margin, 2)]
    homes = [(r, c) for r in (0, h - 1) for c in range(margin, w - margin)]
    grid = Grid(obs, dead_ends=set(homes))
    shelf_set = set(shelves)

    reserved = set(stations) | set(homes)
    pickups = sorted(
        {
            (r, c)
            for (r, c) in grid.free_cells()
            if (r, c) not in reserved
            and any((r + dr, c + dc) in shelf_set for dr, dc in ((-1, 0), (1, 0)))
        }
    )
    return Warehouse(grid, shelves, pickups, stations, homes,
                     meta=dict(block_rows=block_rows, block_cols=block_cols, block_h=block_h,
                               block_w=block_w, aisle=aisle, margin=margin))
