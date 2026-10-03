"""Disruption events and the random disruption schedule generator.

Events are generated up-front from the seed so that different repair strategies
face the same schedule. Concrete cells/agents are chosen at event time from a
pre-shuffled preference list (the first candidate that is valid in the current
state), since what is valid depends on where the robots are.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .grid import Cell, INF


@dataclass
class Event:
    t: int
    kind: str                     # 'blockage' | 'breakdown' | 'emergency'
    duration: float = INF         # INF => permanent (blockage / breakdown)
    cell_prefs: list[Cell] = field(default_factory=list)   # blockage candidates
    agent_prefs: list[int] = field(default_factory=list)   # breakdown / emergency candidates
    pickup: Cell | None = None    # emergency task
    delivery: Cell | None = None
    on_path: bool = False         # blockage: only cells some robot will enter soon
    safe: bool = True             # False: skip the solvability filters (stress tests)

    @property
    def permanent(self) -> bool:
        return self.duration >= INF


@dataclass
class DisruptionConfig:
    obstacle_density: float = 0.02         # fraction of free cells blocked over the run
    blockage_duration: tuple[int, int] = (10, 40)
    blockage_perm_prob: float = 0.2
    n_breakdowns: int = 2
    breakdown_duration: tuple[int, int] = (5, 20)
    breakdown_perm_prob: float = 0.3
    n_emergencies: int = 2
    window: tuple[float, float] = (0.05, 0.7)   # events happen in this fraction of the nominal makespan
    n_blockages: int | None = None     # overrides obstacle_density when set
    blockage_on_path: bool = False     # block a cell some robot is about to use (guaranteed impact)
    safe: bool = True                  # False: disruptions may make tasks unsolvable (stress tests)


def generate_events(rng: np.random.Generator, cfg: DisruptionConfig, makespan: int,
                    blockable: list[Cell], n_agents: int, pickups: list[Cell],
                    stations: list[Cell]) -> list[Event]:
    lo = max(1, int(cfg.window[0] * makespan))
    hi = max(lo + 1, int(cfg.window[1] * makespan))
    events: list[Event] = []

    def when() -> int:
        return int(rng.integers(lo, hi))

    n_block = cfg.n_blockages if cfg.n_blockages is not None \
        else int(round(cfg.obstacle_density * len(blockable)))
    for _ in range(n_block):
        perm = rng.random() < cfg.blockage_perm_prob
        dur = INF if perm else int(rng.integers(cfg.blockage_duration[0], cfg.blockage_duration[1] + 1))
        prefs = [blockable[i] for i in rng.permutation(len(blockable))]
        events.append(Event(when(), 'blockage', dur, cell_prefs=prefs, on_path=cfg.blockage_on_path,
                            safe=cfg.safe))
    for _ in range(cfg.n_breakdowns):
        perm = rng.random() < cfg.breakdown_perm_prob
        dur = INF if perm else int(rng.integers(cfg.breakdown_duration[0], cfg.breakdown_duration[1] + 1))
        events.append(Event(when(), 'breakdown', dur, agent_prefs=[int(a) for a in rng.permutation(n_agents)],
                            safe=cfg.safe))
    for _ in range(cfg.n_emergencies):
        events.append(Event(when(), 'emergency',
                            agent_prefs=[int(a) for a in rng.permutation(n_agents)],
                            pickup=pickups[rng.integers(len(pickups))],
                            delivery=stations[rng.integers(len(stations))], safe=cfg.safe))
    events.sort(key=lambda e: e.t)
    return events
