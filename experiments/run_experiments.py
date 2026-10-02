"""Experiment sweeps. Writes results/<sweep>_runs.csv and results/<sweep>_disruptions.csv.

    python experiments/run_experiments.py                      # all sweeps
    python experiments/run_experiments.py --sweep agents --seeds 5
    python experiments/run_experiments.py --quick              # small smoke version

Sweeps
  agents   number of robots 5..50, fixed dynamic obstacle density
  density  dynamic obstacle density 0..15 % of free cells, fixed number of robots
  single   exactly ONE disruption per run (each type, temporary / permanent), to
           measure "agents whose plans change to handle a single disruption"
  lambda   alter penalty lambda 0..12 at 20 and 40 robots (local vs local-flat): the
           trade-off between total time and robots altered
Every configuration is run with each strategy (local / local-flat / solo / full) on the
same seeds; the agents sweep also runs local-nochain (v2 without chained negotiation).
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import time
import traceback
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from warehouse_mapf.scenario import ScenarioConfig, run_scenario  # noqa: E402

STRATEGIES = ('local', 'local-flat', 'solo', 'full')
SWEEP_STRATEGIES = {'agents': STRATEGIES + ('local-nochain',), 'lambda': ('local', 'local-flat')}
LAMBDAS = (0.0, 1.0, 3.0, 6.0, 12.0)
BASE = dict(tasks_per_agent=3,
            disruptions=dict(obstacle_density=0.03, n_breakdowns=2, n_emergencies=2))

SINGLE_TYPES = {
    'blockage (temp)':  dict(n_blockages=1, blockage_perm_prob=0.0, blockage_on_path=True, n_breakdowns=0, n_emergencies=0),
    'blockage (perm)':  dict(n_blockages=1, blockage_perm_prob=1.0, blockage_on_path=True, n_breakdowns=0, n_emergencies=0),
    'breakdown (temp)': dict(n_blockages=0, n_breakdowns=1, breakdown_perm_prob=0.0, n_emergencies=0),
    'breakdown (perm)': dict(n_blockages=0, n_breakdowns=1, breakdown_perm_prob=1.0, n_emergencies=0),
    'emergency':        dict(n_blockages=0, n_breakdowns=0, n_emergencies=1),
}


def make_jobs(sweep: str, seeds: int, quick: bool) -> list[dict]:
    jobs = []
    if sweep == 'agents':
        for n in ((5, 20) if quick else (5, 10, 20, 30, 40, 50)):
            jobs.append(dict(sweep=sweep, x=n, cfg=dict(BASE, n_agents=n)))
    elif sweep == 'density':
        for d in ((0.0, 0.05) if quick else (0.0, 0.02, 0.05, 0.10, 0.15)):
            c = copy.deepcopy(BASE)
            c['disruptions']['obstacle_density'] = d
            jobs.append(dict(sweep=sweep, x=d, cfg=dict(c, n_agents=20)))
    elif sweep == 'single':
        for n in ((10, 30) if quick else (10, 20, 30, 40, 50)):
            for label, dis in SINGLE_TYPES.items():
                jobs.append(dict(sweep=sweep, x=n, dtype=label,
                                 cfg=dict(n_agents=n, tasks_per_agent=3, disruptions=dis)))
    elif sweep == 'lambda':
        for n in ((20,) if quick else (20, 40)):
            for lam in ((0.0, 6.0) if quick else LAMBDAS):
                jobs.append(dict(sweep=sweep, x=lam, cfg=dict(BASE, n_agents=n),
                                 repair=dict(alter_penalty=lam)))
    out = []
    for j in jobs:
        for strat in SWEEP_STRATEGIES.get(sweep, STRATEGIES):
            for seed in range(seeds):
                jj = copy.deepcopy(j)
                rep = dict(j.get('repair', {}), strategy=strat)
                if strat == 'local-nochain':
                    rep.update(strategy='local', chain_depth=1)
                jj['cfg']['repair'] = rep
                jj.update(strategy=strat, seed=seed)
                out.append(jj)
    return out


def run_job(job: dict) -> tuple[dict | None, list[dict]]:
    try:
        cfg = ScenarioConfig.from_dict(job['cfg'])
        t0 = time.perf_counter()
        sim, m = run_scenario(cfg, job['seed'])
        meta = {k: job[k] for k in ('sweep', 'x', 'strategy', 'seed')}
        meta['dtype'] = job.get('dtype', 'mixed')
        meta['n_agents'] = cfg.n_agents
        meta['density'] = cfg.disruptions.obstacle_density
        meta['alter_penalty'] = cfg.repair.alter_penalty
        row = dict(meta, **m, wall_s=time.perf_counter() - t0)
        drows = [dict(meta, id=r.id, t=r.t, kind=r.kind, permanent=r.permanent,
                      direct=len(r.direct), altered=len(r.altered), collateral=len(r.collateral),
                      max_tier=max(r.tiers.values(), default=0), chain_depth=r.chain_depth,
                      group_replan=6 in r.tiers.values(), messages=r.messages,
                      cpu_ms=1000 * r.cpu)
                 for r in sim.records]
        return row, drows
    except Exception:
        traceback.print_exc()
        return None, []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sweep', choices=['agents', 'density', 'single', 'lambda', 'all'], default='all')
    ap.add_argument('--seeds', type=int, default=10)
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--out', type=Path, default=ROOT / 'results')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sweeps = ['agents', 'density', 'single', 'lambda'] if args.sweep == 'all' else [args.sweep]
    for sweep in sweeps:
        jobs = make_jobs(sweep, args.seeds, args.quick)
        # longest jobs first for better load balancing
        jobs.sort(key=lambda j: -j['cfg']['n_agents'])
        t0 = time.time()
        rows, drows = [], []
        with Pool(args.workers) as pool:
            for i, (row, dr) in enumerate(pool.imap_unordered(run_job, jobs), 1):
                if row is not None:
                    rows.append(row)
                    drows += dr
                    bad = row['collisions'] or not row['all_done'] or row['tasks_done'] + row['tasks_lost'] != row['tasks_total']
                    if bad:
                        print(f'  !! {sweep} x={row["x"]} {row["strategy"]} seed={row["seed"]}: '
                              f'collisions={row["collisions"]} all_done={row["all_done"]} '
                              f'tasks={row["tasks_done"]}/{row["tasks_total"]}', flush=True)
                if i % 10 == 0 or i == len(jobs):
                    print(f'[{sweep}] {i}/{len(jobs)} jobs  {time.time() - t0:.0f}s', flush=True)
        pd.DataFrame(rows).to_csv(args.out / f'{sweep}_runs.csv', index=False)
        pd.DataFrame(drows).to_csv(args.out / f'{sweep}_disruptions.csv', index=False)
        print(f'[{sweep}] done: {len(rows)}/{len(jobs)} runs ok, {len(drows)} disruptions, '
              f'{time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
