"""Demo visuals for the report / presentation.

    python experiments/make_demos.py

results/demo/          mixed-disruption run (20 robots): GIF, interactive replay, snapshots
results/demo_single/   one clean before/after snapshot per disruption type
report/figures/        warehouse overview + copies of the single-disruption snapshots
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'experiments'))

from run_experiments import SINGLE_TYPES  # noqa: E402
from warehouse_mapf.scenario import ScenarioConfig, build  # noqa: E402
from warehouse_mapf.viz.export import export_run  # noqa: E402
from warehouse_mapf.viz.render import (save_animation, save_disruption_snapshot,  # noqa: E402
                                       save_disruption_snapshots, save_frame)

FIG = ROOT / 'report' / 'figures'


def mixed_demo() -> None:
    out = ROOT / 'results' / 'demo'
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cfg = ScenarioConfig.from_dict(dict(n_agents=20, disruptions=dict(obstacle_density=0.03)))
    sim = build(cfg, 1)
    save_frame(sim, 0, FIG / 'warehouse_overview.png')
    sim.run()
    export_run(sim, out / 'replay.json')
    save_disruption_snapshots(sim, out)
    save_animation(sim, out / 'run.gif')
    print('mixed demo:', sim.metrics()['soc'], 'SoC,', len(sim.records), 'disruptions')


def single_demos(n_agents: int = 20) -> None:
    out = ROOT / 'results' / 'demo_single'
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for label, dis in SINGLE_TYPES.items():
        # pick the first seed where the disruption affects someone and local repair negotiates if possible
        best = None
        for seed in range(30):
            sim = build(ScenarioConfig.from_dict(dict(n_agents=n_agents, disruptions=dis)), seed)
            sim.run()
            if not sim.records or not sim.records[0].altered:
                continue
            r = sim.records[0]
            score = (len(r.collateral) > 0, 2 <= len(r.altered) <= 6)
            if best is None or score > best[0]:
                best = (score, sim)
            if score == (True, True):
                break
        if best is None:
            continue
        sim = best[1]
        name = label.replace(' ', '_').replace('(', '').replace(')', '')
        p = out / f'{name}.png'
        save_disruption_snapshot(sim, sim.records[0], p)
        shutil.copy(p, FIG / f'snapshot_{name}.png')
        r = sim.records[0]
        print(f'{label:<17} direct={len(r.direct)} altered={len(r.altered)} collateral={len(r.collateral)}')


if __name__ == '__main__':
    FIG.mkdir(parents=True, exist_ok=True)
    single_demos()
    mixed_demo()
