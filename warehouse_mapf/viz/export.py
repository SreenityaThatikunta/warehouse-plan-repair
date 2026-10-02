"""Export a finished run to JSON and to a self-contained interactive HTML replay."""
from __future__ import annotations

import json
from pathlib import Path

from ..grid import INF

TEMPLATE = Path(__file__).with_name('replay_template.html')


def run_to_dict(sim) -> dict:
    g, wh = sim.grid, sim.wh
    T = sim.t + 1

    def fin(x):
        return None if x >= INF else int(x)

    return dict(
        h=g.h, w=g.w, T=T, strategy=sim.rcfg.strategy,
        shelves=wh.shelves, stations=wh.stations, homes=wh.homes, pickups=wh.pickups,
        agents=[dict(id=a.id, path=[a.pos(t) for t in range(T)], dead=a.dead,
                     deliveries=[t for _, t in a.completed])
                for a in sim.agents],
        blockages=[dict(cell=c, start=s, end=fin(e)) for c, s, e in sim.blockage_log],
        disruptions=[dict(id=r.id, t=r.t, kind=r.kind, permanent=r.permanent, duration=fin(r.duration),
                          cell=r.cell, agent=r.agent, direct=sorted(r.direct), altered=sorted(r.altered),
                          collateral=sorted(r.collateral), tiers={str(k): v for k, v in r.tiers.items()},
                          messages=r.messages,
                          old={str(a): p[r.t:r.t + 40] for a, p in r.old_paths.items()},
                          new={str(a): p[r.t:r.t + 40] for a, p in r.new_paths.items()})
                     for r in sim.records],
        metrics=sim.metrics(),
        n_tasks=sim.n_tasks,
    )


def export_run(sim, path: Path) -> Path:
    data = run_to_dict(sim)
    path = Path(path)
    path.write_text(json.dumps(data, separators=(',', ':')))
    html = TEMPLATE.read_text().replace('/*__RUN_DATA__*/null', json.dumps(data, separators=(',', ':')))
    html_path = path.with_suffix('.html')
    html_path.write_text(html)
    return html_path
