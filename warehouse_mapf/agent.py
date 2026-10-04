from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np

from .grid import Cell, Grid


class Goal(NamedTuple):
    cell: Cell
    kind: str
    task_id: int


@dataclass
class Task:
    id: int
    pickup: Cell
    delivery: Cell


@dataclass
class Agent:
    id: int
    start: Cell
    goals: list[Goal]
    path: list[Cell] = field(default_factory=list)
    ptr: int = 0
    fixed_until: int = 0
    dead: bool = False
    emergency_tasks: set = field(default_factory=set)
    carrying: set = field(default_factory=set)
    completed: list = field(default_factory=list)
    last_delivery: int = 0

    def pos(self, t: int) -> Cell:
        return self.path[min(t, len(self.path) - 1)]

    @property
    def end_time(self) -> int:
        return len(self.path) - 1

    @property
    def is_emergency(self) -> bool:
        return bool(self.emergency_tasks)

    def remaining_goals(self) -> list[Goal]:
        return self.goals[self.ptr:]

    def done(self, t: int) -> bool:
        return self.dead or (self.ptr == len(self.goals) - 1 and t >= self.end_time
                             and self.pos(t) == self.goals[-1].cell)

    def update_progress(self, t: int) -> None:
        if self.dead:
            return
        pos = self.pos(t)
        while self.ptr < len(self.goals) - 1 and pos == self.goals[self.ptr].cell:
            g = self.goals[self.ptr]
            if g.kind == 'pickup':
                self.carrying.add(g.task_id)
            elif g.kind == 'delivery':
                self.carrying.discard(g.task_id)
                self.emergency_tasks.discard(g.task_id)
                self.completed.append((g.task_id, t))
                self.last_delivery = t
            self.ptr += 1


def generate_tasks(rng: np.random.Generator, pickups: list[Cell], stations: list[Cell],
                   n: int, start_id: int = 0) -> list[Task]:
    tasks = []
    for i in range(n):
        p = pickups[rng.integers(len(pickups))]
        d = stations[rng.integers(len(stations))]
        tasks.append(Task(start_id + i, p, d))
    return tasks


def order_tasks_greedy(grid: Grid, start: Cell, tasks: list[Task]) -> list[Task]:
    remaining, ordered, cur = list(tasks), [], start
    while remaining:
        best = min(remaining, key=lambda tk: grid.dist(cur, tk.pickup))
        remaining.remove(best)
        ordered.append(best)
        cur = best.delivery
    return ordered


def goals_from_tasks(tasks: list[Task], home: Cell) -> list[Goal]:
    goals = []
    for tk in tasks:
        goals += [Goal(tk.pickup, 'pickup', tk.id), Goal(tk.delivery, 'delivery', tk.id)]
    goals.append(Goal(home, 'home', -1))
    return goals
