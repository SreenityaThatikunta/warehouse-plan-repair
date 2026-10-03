# Negotiated Local Plan Repair for Multi-Robot Warehouse Pathfinding

A team of warehouse robots on a grid plans collision-free pick-and-deliver routes with **prioritized Space-Time A\***. While they execute those plans, disruptions happen:
- **robot breakdowns** (temporary or permanent)
- **cell blockages** (temporary or permanent)
- **high-priority emergency tasks**

The affected robots **repair their plans locally**. They negotiate with neighbours over a range-limited message bus, never re-run the global planner, and change as few other robots' plans as possible.

- `PLAN.md`: design and algorithms
- `DECISIONS.md`: design choices and the reasons for them
- `PROGRESS.md`: log of the work done
- `report/report.tex` → `report/report.pdf`: the report, including the settings where agents fail

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run one scenario
```bash
.venv/bin/python -m warehouse_mapf.run --agents 20 --density 0.03 --strategy local --seed 1
```

`--strategy` accepts:
- `local`: the proposed method (v2): chained negotiation, a local group replan for urgent robots, and emergency deadlines
- `local-flat`: the v1 method (flat coalitions only), kept for comparison
- `solo`: an ablation with no negotiation
- `full`: the baseline that replans everything from scratch

Add `--live` to replay the finished run in an interactive window (`--fps` sets the speed).

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
| `lambda` | alter penalty λ: 0, 1, 3, 6, 12 (`local` vs `local-flat`) | 20 and 40 robots. Shows the trade-off between robots altered and extra time. |

| `stress` (run separately: `--sweep stress`) | settings built to make robots fail: solvability filters off, 20–30% density, a small crowded map, a short communication radius | 10 seeds. Reports completion rate and stuck robots, labelled *goal cut off* or *deadlock*. |

Every configuration is run with `local`, `local-flat`, `solo` and `full` on the same seeds. The `agents` sweep also runs `local-nochain` (v2 without chained negotiation).

## Results (v2 vs v1)

Full tables: `results/summary.md`. Charts: `report/figures/`. 10 seeds per setting, 0 collisions in all 1,700 runs.

| robots | strategy | SoC | altered / disruption | emergency on time | repair ms |
|---|---|---|---|---|---|
| 30 | v2 `local` | 4093 | 3.52 | 25% | 416 |
| 30 | v1 `local-flat` | 4112 | 3.62 | 20% | 263 |
| 30 | `full` replan | 4131 | 18.29 | 75% | 124 |
| 50 | v2 `local` | 6958 | 5.88 | 35% | 2270 |
| 50 | v1 `local-flat` | 6974 | 5.75 | 15% | 1102 |
| 50 | `full` replan | 7201 | 39.33 | 100% | 590 |

v2 lowers total time and makes more emergencies on time, while changing about as many plans as v1. It takes about twice the repair time. Full replanning changes 5–7× more plans. See PROGRESS.md and DECISIONS D17–D24 for details and caveats.

## Demos
```bash
.venv/bin/python experiments/make_demos.py
```
- `results/demo/`: GIF, interactive `replay.html` and snapshots of a 20-robot run with mixed disruptions
- `results/demo_single/`: a before/after snapshot of each disruption type
- `results/demo_failure/`: GIFs and final-state snapshots of runs where robots fail (goals cut off; `solo` gridlock)

## Report
```bash
cd report && latexmk -pdf report.tex
```

## Tests
```bash
.venv/bin/python -m pytest -q tests
```

The safety tests use strict mode, in which no robot may overwrite another robot's reservation. They also check every committed future plan for conflicts after each disruption.

## Layout
See `PLAN.md` §3.
