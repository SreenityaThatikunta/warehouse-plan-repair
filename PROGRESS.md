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

### 2026-10-02
- Project titled **"Negotiated Local Plan Repair for Multi-Robot Warehouse Pathfinding"**.
- Created the private GitHub repo `SreenityaThatikunta/warehouse-plan-repair` and pushed the code, docs, results, charts and demos (69 files; `.venv` and `.DS_Store` excluded).
- Compared the repo with the peer repo `Sirin-890/autonomus` (branches `main` and `v2`). Wrote the v2 improvement plan in PLAN.md §10: chained negotiation, a local group-replan tier, an emergency deadline, throughput, a PDF report, a live view and a λ sweep. It is waiting for approval.

### 2026-10-03: v2 (ideas from the peer repo, done better)
- **Implemented:**
  - chained negotiation (D17)
  - Tier 3b local group replan for urgent robots (D18)
  - emergency deadline with on-time and lateness metrics (D19)
  - an expanding-ring hold for all strategies (D20)
  - a throughput metric
  - `run --live` (interactive replay window)
  - strategies `local-flat` (v1) and `local-nochain`
  - the λ sweep
  - The PDF report was dropped (D22).
- **Bugs found and fixed while building it:**
  - **Deadlock beyond communication range:** `--agents 40 --seed 1` under the CLI defaults left 2 robots, 16 cells apart, on hold forever. v1 had this too. Fixed by the expanding ring (D20).
  - **Chain recruiting:** blockers were first taken from the member's robot-free path, which for a parked robot is "stay put", so its escape blocker was never found. It now uses the relaxed path (D17). The pocket test covers it.
  - **Tier 3b for every stuck robot** raised altered/disruption by +0.62 at 50 robots, so it is now limited to urgent robots (D18).
- **Tests:** 27 pass, up from 20. New: pocket corridor (a 2-hop chain where v1 can only hold), emergency robots never recruited, tier 3b stays local, deadline metric, and a strict safety run for `local-flat`.
- **Full sweep (10 seeds, 1,700 runs):**
  - 0 collisions.
  - All runs finish except `solo`, 50 robots, permanent breakdown, seed 5. All 49 live robots gridlock on hold from t=40; the same seed failed in v1. `local` and `local-flat` finish it (D24).
- **v2 (`local`) vs v1 (`local-flat`), robots sweep:**
  - **SoC:** −19 at 30 robots, −7 at 40, −16 at 50.
  - **Altered/disruption:** −0.05 at 20, −0.10 at 30, −0.12 at 40, +0.13 at 50 (inside the +0.3 limit).
  - **Emergency on time:** 65% vs 45% at 20 robots, 35% vs 15% at 50. Lateness 9.9 vs 12.1 steps at 50.
  - **Repair time:** about 2× (2.3 s vs 1.1 s per disruption at 50 robots).
- **Density sweep, 10%:**
  - SoC 2768 vs 2792
  - altered 2.63 vs 2.77
  - emergency on time 40% vs 15%
- **Single disruption:** emergency events alter 1.38 robots vs 1.14, because deadline escalation recruits helpers. Permanent breakdowns alter 13.5 vs 13.8.
- **λ sweep:** at 40 robots, v2 with λ=3 beats v1 at every λ on both axes (SoC overhead 345, 4.67 altered). At 20 robots they are about even.
- **Chains ablation (`local-nochain`):** results are within noise of full v2 (at 50 robots: SoC 6963 vs 6958, altered 5.72 vs 5.88, on time 30% vs 35%), at half the repair time. Chains fired in 14 of 60 runs. They are kept on, as requested, but the data does not show a clear gain (D23).
- Committed and pushed v2 as `0ab6ce8`.

### 2026-10-03: failure study, demos, LaTeX report
- **Assignment check:** a codebase, a report and a graphical demo are needed, and the report must point out settings where agents fail. The report was missing, and our sweeps almost never fail because the D16 filters keep every instance solvable. Plan: PLAN.md §11. The report is in LaTeX (user's choice).
- **Stress plumbing:**
  - `DisruptionConfig.safe` switches the D16 filters off
  - stall detection: a run stops after 150 steps with no movement and no goal reached
  - every unfinished robot is classified as `unreachable` (a goal is cut off) or `deadlock`
  - new metrics: `completion`, `stalled`, `stuck_unreachable`, `stuck_deadlock`
- **`stress` sweep** (`--sweep stress`, 760 runs):
  - `unsafe`: 0–20% density, half the events permanent, filters off
  - `unsafe-single`: one permanent blockage or breakdown, filters off
  - `dense`: 20–30% density
  - `crowd`: a small 12×27 map with 20–44 robots
  - `radius`: R = 1–6 at 40 robots
  - **Quick check (2 seeds):** unsafe at 10% leaves `local` at 94% completion (robots whose goals are cut off) and `full` at 74% (also deadlocks). The safe settings did not fail. The full run is in progress.
- **Demos regenerated with v2** (`experiments/make_demos.py`), plus `results/demo_failure/`:
  - `unreachable.gif/png`: unsafe disruptions, seed 0. 57 of 62 tasks done; 3 robots' goals cut off by permanent blockages.
  - `gridlock_solo.gif/png`: `solo`, 50 robots, permanent breakdown, seed 5. 26 of 150 tasks; all 49 live robots deadlocked from t=40, and the stall detector stops the run at t=189. `local` finishes the same instance (150/150).
  - The failure snapshot drops the stuck-to-goal lines when more than 10 robots are stuck; 49 lines hid the map.
- **Report draft:** `report/report.tex` covers problem, environment, method (pseudocode and a TikZ protocol figure), setup, results, the failure section (`report/failures.tex`, pending the stress results), the demo, and limitations.
  - The installed TeX has no `algorithm`/`algpseudocode`, so the pseudocode uses `listings`.
  - **Claim fixed while checking against the data:** Tier 1 settles 94% of repairs at 5 robots but only 57% at 50, not ">90%".
- **Stress sweep done:** 760 runs in 54 min, 0 collisions. Table in `results/summary.md` ("Stress study"); charts `stress_completion.png` and `stress_causes.png`. Results for `local` v2:
  - **unsafe:** completion 96.9% / 91.0% / 80.6% / 26.8% at 0 / 5 / 10 / 20% density. Every strategy fails about equally. Even at 0%, 3 of 10 runs fail, because a permanently broken robot in a 1-wide aisle is a wall.
  - **Knock-on deadlock:** at 10%, 2.3 robots per run have a goal cut off, and another 3.1 deadlock behind them although their goals are reachable (9.9 + 8.8 at 20%). Local does better than `full` here (80.6% vs 71.3%).
  - **unsafe-single:** one permanent breakdown fails 2/10 runs at 20 robots and 4/10 at 40; one permanent blockage fails 1/10.
  - **crowd (small map), the main weakness of our method:** 1/10 runs fail at 30 robots and 3/10 at 40 (73.9%), against 1/10 (94.9%) for `full`.
    - **Mechanism, seed 3:** a temporary breakdown at t=14 hits 6 robots; their Tier 4 holds cascade until all 40 robots hold and 1 of 122 tasks gets done. `full` solves the same instance (122/122).
    - At 44 robots no `local` run fails, so the effect is instance-specific.
  - **dense (filters on):** 1/10 runs fail at 30% (2 deadlocked robots); `full` fails 2/10. No failures up to 25% for v2.
  - **radius R = 1–6:** no failures. SoC is 1% worse at R=1, because the expanding ring compensates.
  - **Emergency deadlines:** on time only 5% on the crowded map with 40 robots and at 30% density.
- **Report:** `report/failures.tex` written (F1 goals cut off, F2 knock-on deadlock, F3 hold cascade, F4 very dense, F5 `solo` gridlock, F6 deadlines, plus where agents did not fail). `report/report.pdf` builds with `latexmk -pdf`: 12 pages, no overfull boxes. I checked the pages visually and enlarged the snapshot figure. LaTeX build files are gitignored.
- Committed and pushed the failure study and the 12-page report as `f84e4fe`.
- **Report cut to 5 pages** (user request). Changes:
  - title lists the team: Sreenitya Thatikunta (B23CS1072), Arpita Deshmukh (B23CM1007), Prajna Agrawal (B23CS1054)
  - no code blocks or pseudocode; the tiers are described in prose and a one-row diagram, and the repo is linked at the end
  - one results table (20 and 50 robots), one figure of plans changed, the stress completion chart and table, and one failure snapshot
  - the failure section is merged into `report.tex`; `report/failures.tex` is removed
  - builds with no overfull boxes; pages checked visually
- **Report revised to 6 pages** (user request):
  - abstract and date removed; it starts directly with the sections
  - added back: the single-disruption and extra-time charts, the λ trade-off chart, the stuck-robot causes chart, and the `solo` gridlock snapshot
  - builds with no overfull boxes; pages checked visually

### 2026-10-04
- Checked the full assignment brief against the deliverables; every requirement is covered. Open items:
  - the GitHub repo linked from the report is **private**, so graders cannot open it
  - the code and the PDF still have to be uploaded to Google Classroom
- **Report:** the Limitations section is now a bullet list (6 pages, no overfull boxes).
- **Demo videos are now MP4** (user request). `save_animation` picks the writer from the extension: `.mp4` uses ffmpeg/H.264, `.gif` uses Pillow. `run.mp4` (37 s), `unreachable.mp4` and `gridlock_solo.mp4` replace the GIFs; `run --viz` also writes `run.mp4`.
- Frame drawing is now one function, `draw_frame`, so a single frame can be saved. New `save_demo_frame` saves a frame with a full legend: `report/figures/demo_frame.png`, taken 2 steps after the disruption that changed the most plans (a permanent blockage, 11 plans).
- **Report:** the Graphical demonstration section now shows that frame (Figure 9). Some figures were shrunk to keep it at 6 pages, with no overfull boxes.
