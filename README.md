# Negotiated Local Plan Repair for Multi-Robot Warehouse Pathfinding

A team of warehouse robots on a grid plans collision-free pick-and-deliver routes with **prioritized Space-Time A\***. While they execute those plans, disruptions happen:
- **robot breakdowns** (temporary or permanent)
- **cell blockages** (temporary or permanent)
- **high-priority emergency tasks**

The affected robots **repair their plans locally**. They negotiate with neighbours over a range-limited message bus, never re-run the global planner, and change as few other robots' plans as possible.

- `PLAN.md`: design and algorithms
- `DECISIONS.md`: design choices and the reasons for them
- `PROGRESS.md`: log of the work done

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run one scenario
```bash
.venv/bin/python -m warehouse_mapf.run --agents 20 --density 0.03 --strategy local --seed 1
```

`--strategy` accepts:
- `local`: the proposed method
- `solo`: an ablation with no negotiation
- `full`: the baseline that replans everything from scratch

Add `--viz --out results/demo` to write the visuals:
- `disruption_XX_*.png`: before/after snapshot of each disruption
- `run.gif`: animation of the whole run
- `replay.html`: interactive replay (open it in a browser)
- `replay.json`

## Experiments
```bash
.venv/bin/python experiments/run_experiments.py            # all sweeps, 10 seeds, runs in parallel
.venv/bin/python -m warehouse_mapf.viz.plots               # charts -> report/figures, tables -> results/summary.md
```

| Sweep | What varies | What is fixed |
|---|---|---|
| `agents` | robots: 5, 10, 20, 30, 40, 50 | 3% dynamic obstacle density, 2 breakdowns, 2 emergencies per run |
| `density` | dynamic obstacle density: 0–15% of free cells | 20 robots |
| `single` | exactly one disruption per run, for each type (temporary or permanent blockage or breakdown, emergency) | 10–50 robots. This gives the number of plans altered to handle a single disruption. |

Every configuration is run with all 3 strategies on the same seeds.

## Tests
```bash
.venv/bin/python -m pytest -q tests
```

The safety tests use strict mode, in which no robot may overwrite another robot's reservation. They also check every committed future plan for conflicts after each disruption.

## Layout
See `PLAN.md` §3.
