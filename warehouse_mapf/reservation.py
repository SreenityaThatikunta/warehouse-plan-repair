from __future__ import annotations

from collections import defaultdict

from .grid import Cell, INF


class ReservationTable:
    strict = False

    def __init__(self):
        self.cell_res: dict[Cell, dict[int, int]] = defaultdict(dict)
        self.edge_res: dict[tuple[Cell, Cell, int], int] = {}
        self.parked: dict[Cell, tuple[int, int]] = {}
        self._ends: dict[int, int] = {}

    @property
    def max_time(self) -> int:
        return max(self._ends.values(), default=0)

    def add_path(self, aid: int, path: list[Cell], from_t: int = 0) -> None:
        for t in range(max(from_t, 0), len(path)):
            if self.strict:
                owner = self.cell_res[path[t]].get(t)
                if owner is not None and owner != aid:
                    raise AssertionError(f'agent {aid} overwrites agent {owner} at {path[t]}, t={t}')
            self.cell_res[path[t]][t] = aid
            if t + 1 < len(path) and path[t] != path[t + 1]:
                self.edge_res[(path[t], path[t + 1], t)] = aid
        self.parked[path[-1]] = (aid, len(path) - 1)
        self._ends[aid] = len(path) - 1

    def remove_path(self, aid: int, path: list[Cell], from_t: int = 0) -> None:
        for t in range(max(from_t + 1, 0), len(path)):
            res = self.cell_res.get(path[t])
            if res is not None and res.get(t) == aid:
                del res[t]
        for t in range(max(from_t, 0), len(path) - 1):
            key = (path[t], path[t + 1], t)
            if self.edge_res.get(key) == aid:
                del self.edge_res[key]
        p = self.parked.get(path[-1])
        if p is not None and p[0] == aid:
            del self.parked[path[-1]]
        if aid in self._ends:
            self._ends[aid] = min(self._ends[aid], max(from_t, 0))

    def park(self, aid: int, cell: Cell, t: int) -> None:
        self.parked[cell] = (aid, t)

    def unpark(self, aid: int, cell: Cell) -> None:
        p = self.parked.get(cell)
        if p is not None and p[0] == aid:
            del self.parked[cell]

    def can_park(self, cell: Cell, t: int, aid: int) -> bool:
        p = self.parked.get(cell)
        if p is not None and p[0] != aid:
            return False
        res = self.cell_res.get(cell)
        if res:
            for tt, owner in res.items():
                if tt > t and owner != aid:
                    return False
        return True

    def owners_at(self, cell: Cell, t_from: int, t_to: float = INF) -> dict[int, int]:
        out: dict[int, int] = {}
        for tt, owner in self.cell_res.get(cell, {}).items():
            if t_from <= tt <= t_to:
                out[owner] = min(out.get(owner, INF), tt)
        p = self.parked.get(cell)
        if p is not None and p[1] <= t_to:
            out[p[0]] = min(out.get(p[0], INF), max(p[1], t_from))
        return out


class Blockages:
    def __init__(self):
        self.intervals: dict[Cell, list[tuple[int, float]]] = defaultdict(list)
        self.max_known_time = 0

    def add(self, cell: Cell, start: int, end: float) -> None:
        self.intervals[cell].append((start, end))
        self.max_known_time = max(self.max_known_time, start, end if end < INF else start)

    def is_blocked(self, cell: Cell, t: int) -> bool:
        iv = self.intervals.get(cell)
        if not iv:
            return False
        return any(s <= t < e for s, e in iv)

    def can_park(self, cell: Cell, t: int) -> bool:
        iv = self.intervals.get(cell)
        return not iv or all(e <= t for _, e in iv)

    def permanent_cells(self) -> set[Cell]:
        return {c for c, iv in self.intervals.items() if any(e >= INF for _, e in iv)}

    def active_at(self, t: int) -> list[Cell]:
        return [c for c, iv in self.intervals.items() if any(s <= t < e for s, e in iv)]
