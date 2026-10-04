from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

from .scenario import ScenarioConfig, run_scenario


def main() -> None:
    ap = argparse.ArgumentParser(description='run one warehouse scenario and print metrics')
    ap.add_argument('--config', type=Path)
    ap.add_argument('--agents', type=int)
    ap.add_argument('--density', type=float, help='dynamic obstacle density (fraction of free cells)')
    ap.add_argument('--breakdowns', type=int)
    ap.add_argument('--emergencies', type=int)
    ap.add_argument('--strategy', choices=['local', 'local-flat', 'solo', 'full'])
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--viz', action='store_true', help='write snapshots, GIF and replay JSON to --out')
    ap.add_argument('--out', type=Path, default=Path('results/run'))
    ap.add_argument('--live', action='store_true', help='replay the run in an interactive matplotlib window')
    ap.add_argument('--fps', type=int, default=8, help='frames per second for --live')
    args = ap.parse_args()
    if args.live:
        os.environ['WAREHOUSE_LIVE'] = '1'

    cfg = ScenarioConfig.from_dict(yaml.safe_load(args.config.read_text()) if args.config else {})
    if args.agents is not None:
        cfg.n_agents = args.agents
    if args.density is not None:
        cfg.disruptions.obstacle_density = args.density
    if args.breakdowns is not None:
        cfg.disruptions.n_breakdowns = args.breakdowns
    if args.emergencies is not None:
        cfg.disruptions.n_emergencies = args.emergencies
    if args.strategy:
        cfg.repair.strategy = args.strategy

    sim, m = run_scenario(cfg, args.seed)
    print(json.dumps(m, indent=2))
    print('\nPer-disruption:')
    for r in sim.records:
        what = f'agent {r.agent}' if r.agent is not None else f'cell {r.cell}'
        dur = '-' if r.kind == 'emergency' else 'perm' if r.permanent else f'{int(r.duration)} steps'
        print(f'  #{r.id:<3} t={r.t:<4} {r.kind:<9} {what:<14} {dur:<9} direct={len(r.direct)} '
              f'altered={len(r.altered)} collateral={len(r.collateral)} tiers={sorted(set(r.tiers.values()))}'
              + (f' chain={r.chain_depth}' if r.chain_depth >= 2 else ''))
    if args.viz:
        from .viz.export import export_run
        from .viz.render import save_animation, save_disruption_snapshots
        args.out.mkdir(parents=True, exist_ok=True)
        export_run(sim, args.out / 'replay.json')
        save_disruption_snapshots(sim, args.out)
        save_animation(sim, args.out / 'run.mp4')
        print(f'\nVisual outputs written to {args.out}/')
    if args.live:
        from .viz.render import show_live
        show_live(sim, fps=args.fps)


if __name__ == '__main__':
    main()
