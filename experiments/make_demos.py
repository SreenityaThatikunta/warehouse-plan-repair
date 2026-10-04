"""Demo visuals for the report / presentation.

    python experiments/make_demos.py

results/demo/          mixed-disruption run (20 robots): MP4, interactive replay, snapshots
results/demo_single/   one clean before/after snapshot per disruption type
results/demo_failure/  runs where robots FAIL to finish: goals cut off (unsafe disruptions)
                       and a fleet gridlock (solo ablation): MP4 + final-state snapshot
report/figures/        warehouse overview + copies of the single-disruption and failure snapshots
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
from warehouse_mapf.viz.render import (save_animation, save_demo_frame, save_disruption_snapshot,  # noqa: E402
                                       save_disruption_snapshots, save_failure_snapshot, save_frame)

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
    save_animation(sim, out / 'run.mp4')
    # report screenshot: a frame shortly after the disruption that changed the most plans
    rec = max(sim.records, key=lambda r: len(r.altered))
    save_demo_frame(sim, rec.t + 2, FIG / 'demo_frame.png')
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


def failure_demos() -> None:
    out = ROOT / 'results' / 'demo_failure'
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    # 1. Unsafe disruptions (solvability filters off): permanent blockages cut goals off.
    unsafe = dict(obstacle_density=0.10, safe=False, blockage_perm_prob=0.5, breakdown_perm_prob=0.5,
                  n_breakdowns=2, n_emergencies=2)
    for seed in range(20):
        sim = build(ScenarioConfig.from_dict(dict(n_agents=20, disruptions=unsafe)), seed)
        m = sim.run()
        if not m['all_done'] and m['stuck_unreachable']:
            break
    save_failure_snapshot(sim, out / 'unreachable.png',
                          f'Failure: unsafe disruptions (10% density, 50% permanent), 20 robots, local v2, seed {seed}')
    save_animation(sim, out / 'unreachable.mp4')
    shutil.copy(out / 'unreachable.png', FIG / 'failure_unreachable.png')
    print(f'unreachable demo: seed {seed}, {m["tasks_done"]}/{m["tasks_total"]} tasks, '
          f'{m["stuck_unreachable"]} cut off, {m["stuck_deadlock"]} deadlocked')
    # 2. Fleet gridlock without negotiation (solo ablation), one permanent breakdown, 50 robots.
    cfg = ScenarioConfig.from_dict(dict(n_agents=50, tasks_per_agent=3, repair=dict(strategy='solo'),
                                        disruptions=SINGLE_TYPES['breakdown (perm)']))
    sim = build(cfg, 5)
    m = sim.run()
    save_failure_snapshot(sim, out / 'gridlock_solo.png',
                          'Failure: gridlock without negotiation (solo ablation), 50 robots, 1 permanent breakdown, seed 5')
    save_animation(sim, out / 'gridlock_solo.mp4')
    shutil.copy(out / 'gridlock_solo.png', FIG / 'failure_gridlock_solo.png')
    print(f'gridlock demo: {m["tasks_done"]}/{m["tasks_total"]} tasks, stalled={m["stalled"]}, '
          f'{m["stuck_deadlock"]} deadlocked')
    # Same instance with local repair, for contrast.
    cfg.repair.strategy = 'local'
    sim = build(cfg, 5)
    m = sim.run()
    save_frame(sim, sim.t, out / 'gridlock_local_final.png')
    print(f'same instance, local: {m["tasks_done"]}/{m["tasks_total"]} tasks, all_done={m["all_done"]}')


if __name__ == '__main__':
    FIG.mkdir(parents=True, exist_ok=True)
    single_demos()
    mixed_demo()
    failure_demos()
