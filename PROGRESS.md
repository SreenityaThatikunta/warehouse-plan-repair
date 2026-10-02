# Progress

## Milestones
- [x] M0: Planning (`PLAN.md`, `PROGRESS.md`, `DECISIONS.md`)
- [x] M1: Grid, warehouse generator, tasks, agents (`grid.py`, `agent.py`)
- [x] M2: Space-Time A*, reservation table, prioritized MAPF (`st_astar.py`, `reservation.py`, `prioritized.py`)
- [x] M3: Simulator and metrics (`simulator.py`, `scenario.py`, CLI `run.py`)
- [x] M4: Disruption events, generator, affected-agent detection (`events.py`, `simulator.handle_event`)
- [x] M5: Repair Tier 1 (solo replan). Tier 4 (hold and retry) is also done
- [x] M6: Comms bus, negotiation (Tiers 2–3), task reallocation auction (`comms.py`, `repair.py`)
- [x] M7: Baselines: full replan, plus a solo-only ablation
- [x] M8: Visualization: snapshots, GIF, interactive HTML replay, experiment charts (`viz/`, `experiments/make_demos.py`)
- [x] M9: Experiment sweeps: 1,080 runs, results in `results/*.csv` + `results/summary.md`
- [ ] M10: Report (postponed on request)

## Current status
- **Done:**
  - all code
  - 20 tests passing
  - final sweeps (10 seeds)
  - charts in `report/figures/`
  - demo visuals in `results/demo*`
- **Next:** the report (M10), when the user asks for it.
- **Known limitation:** 1 of 1,080 runs (the solo ablation, 50 robots, single permanent breakdown in the side corridor) gridlocks. Local repair and full replan both recover on that same case. This is reported as a finding, not hidden.

## Key results (10 seeds per setting)
| | Local repair (ours) | Solo only | Full replan |
|---|---|---|---|
| Plans altered per disruption, 50 robots | **5.8** | 4.6 | 39.7 |
| Extra time steps caused by disruptions, 50 robots | **394** | 508 | 637 |
| Plans altered per single disruption (10–50 robots pooled) | 1.1 (emergency) to 13.8 (permanent breakdown) | about the same | 22–29 for every type |
| Collisions across 1,080 runs | 0 | 0 | 0 |

- **More robots:** the number of plans altered grows slowly with local repair (2.7 at 10 robots, 9.5 at 50 for a single disruption). With full replan it grows almost in proportion to fleet size (5.4, then 46).
- **Higher obstacle density:**
  - total time rises for all strategies
  - plans altered per disruption stays about 2.1–2.8 for local repair, against 8–13 for full replan
  - at 15% density, local repair has the lowest total time
- **Cost of negotiation:** repair time is higher for local repair (about 1.5 s per disruption at 50 robots, against 0.9 s for full replan and 0.1 s for solo).

## Log
### 2026-09-24
- Read the assignment and wrote the full plan in `PLAN.md`: architecture, algorithms, repair tiers, metrics, experiments.
- **Decisions confirmed with the user:**
  - Python
  - 4-connected moves, and picking up or dropping off takes no extra time
  - the report comes later
  - graphics: both warehouse visuals (snapshots, GIF, interactive replay) and experiment charts
- **Core built:**
  - Kiva-style warehouse map with shelf blocks, 1-wide aisles, stations on the side walls and docks on the top and bottom walls
  - multi-goal ST-A* (`(cell, goal_idx, t)`) with a true-distance heuristic
  - reservation table covering vertex, edge-swap and parking
  - prioritized planner with random restarts
- **Simulator built:**
  - discrete-time execution with collision checks
  - blockage, breakdown (temporary or permanent) and emergency events
  - on permanent breakdown, the robot's tasks are auctioned to its neighbours
- **Repair built:**
  - local tiered repair (solo, then negotiation at radius R, then 2R, then hold)
  - solo-only ablation
  - full-replan baseline
- **Bugs fixed along the way:**
  - **Full replan was slow and failing.**
    - Searches were running out of budget when a robot's home cell was still used by other robots later.
    - Fix: the heuristic now includes the earliest time the robot is allowed to park.
    - Fix: docks are now dead-end bays, so nobody drives through them.
  - **Collision from a stale plan.** Restoring the old plan of a negotiation partner that was itself waiting for repair overwrote another robot's reservation. Fixes:
    - robots in the repair queue can't be negotiation partners
    - reservations are cleared as soon as a breakdown invalidates a plan
    - `strict` mode to catch reservation overwrites
    - plan validator (`Simulator.validate_plans`)
- **Performance:**
  - `max_time` in the reservation table is now tracked exactly, instead of only ever growing
  - searches that can't succeed are detected quickly with a reachability check
  - repair searches have a capped budget
  - the A* inner loop is inlined
  - negotiation tries only blockers, smallest group first, and stops early once a group beats going solo
  - result: a 30-robot run went from over 10 min (it didn't finish) to 15 s
- **Deadlock found:** an emergency pickup cell had been permanently blocked earlier, so the robot waited forever and others queued behind it. Fixes:
  - emergency cells are checked for reachability when the event fires
  - robots on hold can be asked to move during negotiation
- **More deadlocks fixed:**
  - A permanently broken robot's object was handed off at an unreachable dock. The hand-off cell must now be reachable and not a dock or station; otherwise the task is recorded as lost.
  - Two robots on hold could block each other forever. A robot on hold can now always negotiate on retry, under every strategy; this is the deadlock breaker.
  - If the repair loop hits its safety cap, the remaining robots hold and retry next step. This is counted in `guard_hits`.
- All 8 previously failing seeds now pass: 0 collisions and every task finished. The full sweep is rerunning.
- **First sweep (10 seeds):** local repair changes about 0 extra robots per disruption, against 9–13 for full replan, with similar total time. Final numbers will come after the rerun.
- **Full-replan restore bug fixed:** when full replan failed, restoring the old plans re-added plans that the disruption had already invalidated.
- **Final sweep rerun:** 1,079 of 1,080 runs are clean.
- **Final outputs:**
  - `viz/plots.py` writes 12 charts and `results/summary.md`
  - `experiments/make_demos.py` writes a snapshot for each disruption type plus the mixed demo
  - README, PLAN and DECISIONS are updated to match the implementation
- **Demo graphics generated** in `results/demo/` for 20 robots, seed 1:
  - disruption snapshots
  - `run.gif`
  - `replay.html`
