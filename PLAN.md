# Plan — Multi-Agent Warehouse Pathfinding with Local Plan Repair

## 1. Problem summary

- Warehouse floor is a 2D grid. Robots move one cell per time step (4-connected) or wait.
- Each robot starts at a cell and has a list of tasks: go to a pickup cell, then carry the object to its delivery cell.
- An initial joint plan comes from a multi-agent pathfinding (MAPF) algorithm: **prioritized planning with Space-Time A\***.
- While the plan runs, disruptions happen:
  1. **Agent breakdown**: a robot stops, either for a while or for good.
  2. **Cell blockage**: a free cell becomes an obstacle, either for a while or for good.
  3. **Emergency task**: a robot is given an urgent pickup and delivery that must happen before its other tasks.
- **Repair rules**:
  - Disrupted agents talk to nearby agents and agree on new paths between themselves.
  - Global planning is **never** re-run from scratch.
  - Change the plans of **as few agents as possible**.
- **What we measure**:
  - Total time steps for all agents to finish their tasks (sum of costs; makespan is also reported).
  - Number of agents whose plans had to change for each single disruption.
  - How performance changes as the number of agents grows.
  - How performance changes as the density of dynamic obstacles grows.

## 2. Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3 (3.11+ compatible) |
| Core deps | `numpy`, `matplotlib` (plots + animation), `pyyaml` (configs), `pandas` (results) |
| Testing | `pytest` |
| Visualization | matplotlib animation → GIF/MP4, plus a self-contained interactive HTML replay viewer (reads the JSON run log) |
| Report | Markdown source → PDF (and/or DOCX) with the figures embedded |

## 3. Architecture (as implemented, flat package)

```
warehouse_mapf/
  grid.py          # Grid (4-connected, dead-end docks), BFS distance maps, warehouse layout generator
  agent.py         # Goal/Task/Agent: goal queue, executed plan, progress tracking, greedy task ordering
  reservation.py   # ReservationTable (vertex, edge-swap, parking) + Blockages (dynamic obstacle intervals)
  st_astar.py      # Multi-goal Space-Time A* (earliest-park bound, time-collapsed closed set)
  prioritized.py   # Prioritized planning (initial MAPF + full-replan baseline)
  events.py        # Disruption events + seeded schedule generator (density, types, durations)
  comms.py         # Range-limited message bus (ALERT/STATUS/REQUEST/PROPOSE/COMMIT/... counted)
  repair.py        # LocalRepair (tiers 1-4), SoloRepair ablation, FullReplan baseline
  simulator.py     # Execution loop, event application, task auction, collision checks, metrics
  scenario.py      # Config dataclasses + reproducible scenario builder
  run.py           # CLI for a single run (+ --viz)
  viz/
    render.py      # Disruption snapshots (before/after) + GIF animation (matplotlib)
    export.py      # JSON + self-contained interactive HTML replay
    replay_template.html
    plots.py       # Experiment charts + results/summary.md tables
experiments/run_experiments.py   # Parallel sweeps -> results/*_runs.csv, *_disruptions.csv
tests/test_core.py               # Unit + safety/integration tests (strict reservation + plan validation)
```

## 4. Core algorithms

### 4.1 Initial planning: prioritized Space-Time A*
- Agents are planned one at a time in priority order. The default order puts the longest task chain first.
- Each finished path is added to a reservation table:
  - a **vertex** reservation `(cell, t)`
  - an **edge** reservation `(u→v, t)`, which stops two agents swapping cells
- ST-A* searches over states `(x, y, t)`. At each step an agent can move to one of 4 neighbours or wait.
- The heuristic is the precomputed true distance to the next goal: a BFS from that goal on the static map.
- A task has several goals in a row. The search treats this as `(x, y, goal_index, t)` so the whole task chain is one path.
- After its last task, a robot returns to its own dock and parks there, and that cell is reserved from then on. Docks are dead-end bays (DECISIONS D10).

### 4.2 Disruption model
| Event | Parameters | Effect |
|---|---|---|
| Breakdown | agent, t, duration (∞ = permanent) | The agent stays put and its cell becomes an obstacle for that duration. If the breakdown is permanent, its remaining tasks are handed to other agents. |
| Cell blockage | cell, t, duration | The cell becomes impassable for `[t, t+duration)`. |
| Emergency task | agent, t, pickup, delivery | The new task goes to the front of the agent's goal queue. |

The **dynamic obstacle density** is the fraction of free cells that get blocked over the run. It can also be expressed as a blockage rate per time step. This is the variable we sweep in experiments.

### 4.3 Plan repair: local, negotiation-based, minimum change (as implemented)
When a disruption happens at time `t`:

1. **Detect.** The robots affected directly are:
   - the robot that broke down or received the emergency task
   - every robot whose reservations use the blocked or broken cell during the blocked interval
   - on a permanent breakdown, the robots that win its tasks in the auction
2. **Tier 1: solo replan.**
   - The robot replans all its remaining goals with ST-A*, treating everyone else's reservations as fixed.
   - Its delay is compared with the best path that ignores other robots.
   - If the delay is at most `δ` (4, or 0 for emergency robots), the new plan is committed. Only this robot changes.
3. **Tier 2: negotiation within radius `R` (6).**
   - The robot sends an ALERT to its neighbours, and each replies with a STATUS.
   - Candidates are neighbours that can move and whose reservations block the robot's ideal path.
   - Coalitions of size 1, 2, 3 are tried in turn. For each, the robot REQUESTs a joint replan in which it has priority, and each member PROPOSEs its new plan and delay.
   - The first size whose best coalition satisfies `delay + λ·|S| < solo delay` is committed with COMMIT/ACCEPT; the other asked robots get REJECT.
4. **Tier 3:** the same negotiation with radius `2R`.
5. **Otherwise:**
   - If a solo detour exists, it is taken, however long it is.
   - If none exists, **Tier 4 (hold)**: the robot stays on its cell and is retried every step, with negotiation always allowed so deadlocks can clear. Robots whose plans pass through the held cell are repaired in cascade.
6. **Count.** Every robot whose future path differs from its plan just before the disruption counts as altered. Cascades and later retries count toward the same disruption.

**Permanent breakdown.**
- The broken cell becomes an obstacle.
- The broken robot's tasks are auctioned to robots within `R`, and the lowest marginal cost wins.
- A carried item is handed off at a reachable free cell next to the broken robot.

### 4.4 Baselines for comparison (as implemented)
- **Full replan (`full`):** re-run prioritized ST-A* for all live robots from the current state. It is a reference only; this is exactly what the assignment forbids for repair.
- **Solo only (`solo`):** an ablation. Tier 1 with no negotiation, except that deadlock-breaking retries may still negotiate. It replaced the planned wait-only baseline, because ST-A* already includes waiting (see DECISIONS D8).

## 5. Metrics

| Metric | Definition |
|---|---|
| Sum of costs (SoC) | Total time steps used by all agents to finish all their tasks. This is the main metric. |
| Makespan | The time step at which the last agent finishes. |
| Agents altered / disruption | The number of agents whose remaining plan after the repair differs from their plan just before it. Reported both with and without the disrupted agent itself. |
| Repair cost overhead | SoC with disruptions minus SoC of the undisrupted plan. |
| Repair CPU time | Wall-clock time spent on each repair. |
| Messages / disruption | Communication overhead. |
| Tier distribution | Which repair tier settled each disruption. |
| Safety | Vertex and edge collisions, which must be 0. |
| Task completion rate | Should be 100% unless an agent is permanently stranded. |

## 6. Experiments

- **Maps:**
  - small warehouse, 20×20
  - medium warehouse, 32×32
  - optional large warehouse, 48×48
  - All use shelf blocks with aisles and pick/drop stations along the shelves.
- **Sweep 1, number of agents:** 5, 10, 20, 30, 40, 50, with obstacle density fixed.
- **Sweep 2, dynamic obstacle density:** 0%, 1%, 2%, 5%, 10% of free cells, with the number of agents fixed.
- **Sweep 3, disruption type:** breakdown, blockage and emergency, each run on its own.
- **Comparison:** local repair against full replan and wait-only on the same seeds.
- **Seeds:** 10–20 per setting. Report mean ± standard deviation or a 95% CI.
- **Outputs:** `results/*.csv`, and in `report/figures/`:
  - SoC vs #agents
  - agents altered vs #agents
  - SoC vs density
  - agents altered vs density
  - tier-distribution bar chart
  - repair-time chart

## 7. Deliverables

1. **Codebase**: modular, tested, with a CLI:
   - `python -m warehouse_mapf.run --config ...`
   - `python experiments/run_experiments.py`
2. **Graphical representation**:
   - Animated GIF/MP4 of example runs showing each disruption type. The original path is drawn dashed and the repaired path solid, and altered agents are highlighted.
   - An interactive HTML replay viewer.
   - The experiment charts.
3. **Report**:
   - problem, approach, algorithms (with pseudocode)
   - repair protocol diagram
   - experimental setup, results and discussion
   - limitations and future work

## 8. Milestones

| # | Milestone | Output |
|---|---|---|
| M1 | Grid, warehouse generator, tasks, agents | `core/` + tests |
| M2 | ST-A* + reservation table + prioritized MAPF | Valid collision-free initial plans |
| M3 | Simulator + metrics (no disruptions) | Baseline SoC/makespan |
| M4 | Disruption events + generator + detector | Events injected and affected agents found |
| M5 | Repair Tiers 0–1 | Solo repair working |
| M6 | Comms + negotiation (Tiers 2–3) + task reallocation | Full repair pipeline |
| M7 | Baselines (full replan, wait-only) | Comparison ready |
| M8 | Visualization (animation + web viewer) | Visual demos |
| M9 | Experiment sweeps + plots | CSVs + figures |
| M10 | Report | Final PDF/DOCX |

## 9. Tracking docs

- `PLAN.md`: this file. It is the design and the plan, updated when the design changes.
- `PROGRESS.md`: milestone checklist and a dated log of work done.
- `DECISIONS.md`: design decisions and the reasons for them, including assumptions and interpretations of the task.

## 10. Assumptions and open questions

- **Movement:** 4-connected moves plus wait, and every action costs 1 time step.
- **Collisions:** vertex and edge (swap) conflicts are forbidden. Following another agent closely is allowed.
- **Loading time:** picking up and dropping off take 0 extra steps, since the agent just has to reach the cell. This can be made configurable.
- **Carrying:** an agent carries one object at a time and does its tasks in the order assigned. The order is set greedily by nearest-next.
- **Communication:** agents within Manhattan radius `R` can talk, with `R = 5` by default. Messages arrive instantly within a time step.
- **Unchanged tasks:** "original plan" means the agent's plan just before the disruption happened.
