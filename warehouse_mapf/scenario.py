from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .agent import Agent, generate_tasks, goals_from_tasks, order_tasks_greedy
from .events import DisruptionConfig, generate_events
from .grid import make_warehouse
from .simulator import RepairConfig, Simulator


@dataclass
class ScenarioConfig:
    n_agents: int = 20
    tasks_per_agent: int = 3
    map: dict = field(default_factory=lambda: dict(block_rows=5, block_cols=5, block_h=2, block_w=5,
                                                     aisle=1, margin=2))
    disruptions: DisruptionConfig = field(default_factory=DisruptionConfig)
    repair: RepairConfig = field(default_factory=RepairConfig)

    @classmethod
    def from_dict(cls, d: dict) -> ScenarioConfig:
        d = dict(d)
        dis = DisruptionConfig(**{k: tuple(v) if isinstance(v, list) else v
                                  for k, v in d.pop('disruptions', {}).items()})
        rep = RepairConfig(**d.pop('repair', {}))
        base = cls()
        m = {**base.map, **d.pop('map', {})}
        return cls(map=m, disruptions=dis, repair=rep, **d)


_WAREHOUSE_CACHE: dict = {}


def build(cfg: ScenarioConfig, seed: int) -> Simulator:
    key = tuple(sorted(cfg.map.items()))
    wh = _WAREHOUSE_CACHE.get(key)
    if wh is None:
        wh = _WAREHOUSE_CACHE[key] = make_warehouse(**cfg.map)
    grid = wh.grid
    rng = np.random.default_rng(seed)
    if cfg.n_agents > len(wh.homes):
        raise ValueError(f'map has only {len(wh.homes)} docks for {cfg.n_agents} agents')

    homes = [wh.homes[i] for i in rng.choice(len(wh.homes), cfg.n_agents, replace=False)]
    tasks = generate_tasks(rng, wh.pickups, wh.stations, cfg.n_agents * cfg.tasks_per_agent)
    agents = []
    for i, home in enumerate(homes):
        mine = order_tasks_greedy(grid, home, tasks[i * cfg.tasks_per_agent:(i + 1) * cfg.tasks_per_agent])
        agents.append(Agent(i, home, goals_from_tasks(mine, home)))

    sim = Simulator(wh, agents, [], cfg.repair, seed=seed, n_tasks=len(tasks))
    if not sim.initial_plan():
        raise RuntimeError(f'initial planning failed (seed={seed})')

    reserved = set(wh.homes) | set(wh.stations)
    blockable = [c for c in grid.free_cells() if c not in reserved]
    ev_rng = np.random.default_rng(seed * 1000 + 17)
    sim.events = generate_events(ev_rng, cfg.disruptions, sim.nominal['makespan'], blockable,
                                 cfg.n_agents, wh.pickups, wh.stations)
    return sim


def run_scenario(cfg: ScenarioConfig, seed: int) -> tuple[Simulator, dict]:
    sim = build(cfg, seed)
    m = sim.run()
    return sim, m
