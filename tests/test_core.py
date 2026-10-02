import numpy as np
import pytest

from warehouse_mapf.events import Event
from warehouse_mapf.grid import INF, Grid, make_warehouse
from warehouse_mapf.prioritized import PlanRequest, plan_prioritized
from warehouse_mapf.reservation import Blockages, ReservationTable
from warehouse_mapf.scenario import ScenarioConfig, build
from warehouse_mapf.st_astar import st_astar


def open_grid(h=5, w=5):
    return Grid(np.zeros((h, w), dtype=bool))


def valid_path(grid, path):
    return all(b == a or b in grid.neighbors(a) for a, b in zip(path, path[1:]))


# ---------------------------------------------------------------- grid / map
def test_warehouse_layout():
    wh = make_warehouse()
    g = wh.grid
    assert len(wh.homes) >= 50
    free = set(g.free_cells())
    assert set(wh.pickups) <= free and set(wh.stations) <= free and set(wh.homes) <= free
    # docks are dead ends: no lateral moves between neighbouring docks
    h = wh.homes[5]
    assert all(n[1] == h[1] for n in g.neighbors(h))
    # everything that matters is connected
    reach = g.reachable(wh.homes[0], set())
    assert set(wh.pickups) | set(wh.stations) | set(wh.homes) <= reach


# ------------------------------------------------------------------ ST-A*
def test_st_astar_shortest_multigoal():
    g = open_grid()
    p = st_astar(g, (0, 0), 0, [(0, 4), (4, 4)], aid=0)
    assert p[0] == (0, 0) and p[-1] == (4, 4) and (0, 4) in p
    assert len(p) - 1 == 8 and valid_path(g, p)


def test_st_astar_respects_vertex_and_swap():
    g = Grid(np.array([[0, 0, 0]], dtype=bool))   # 1x3 corridor
    rt = ReservationTable()
    rt.add_path(1, [(0, 2), (0, 1), (0, 0)])       # agent 1 walks right-to-left
    p = st_astar(g, (0, 0), 0, [(0, 2)], aid=0, rt=rt)
    assert p is None                                # head-on in a corridor with no escape


def test_st_astar_waits_for_blockage():
    g = Grid(np.array([[0, 0, 0]], dtype=bool))
    blk = Blockages()
    blk.add((0, 1), 0, 5)
    p = st_astar(g, (0, 0), 0, [(0, 2)], aid=0, blockages=blk)
    assert p is not None and p.index((0, 1)) >= 5 and len(p) - 1 == 6


def test_st_astar_goal_parked_by_other_is_infeasible():
    g = open_grid()
    rt = ReservationTable()
    rt.add_path(1, [(2, 2)])
    assert st_astar(g, (0, 0), 0, [(2, 2)], aid=0, rt=rt) is None


def test_st_astar_waits_until_goal_is_free():
    g = open_grid()
    rt = ReservationTable()
    rt.add_path(1, [(4, 0), (4, 1), (4, 2), (3, 2), (2, 2), (1, 2), (0, 2), (0, 3), (0, 4)])
    p = st_astar(g, (2, 0), 0, [(2, 2)], aid=0, rt=rt)
    assert p[-1] == (2, 2) and len(p) - 1 >= 4     # can only park after agent 1 has passed at t=4


# ------------------------------------------------------------- prioritized
def test_prioritized_collision_free():
    wh = make_warehouse()
    rng = np.random.default_rng(3)
    homes = [wh.homes[i] for i in rng.choice(len(wh.homes), 20, replace=False)]
    reqs = [PlanRequest(i, h, 0, [wh.pickups[rng.integers(len(wh.pickups))], h], []) for i, h in enumerate(homes)]
    rt = ReservationTable()
    plans = plan_prioritized(wh.grid, rt, None, reqs, rng)
    assert plans is not None
    T = max(len(p) for p in plans.values()) + 1
    at = lambda p, t: p[min(t, len(p) - 1)]
    for t in range(T):
        cells = [at(p, t) for p in plans.values()]
        assert len(cells) == len(set(cells))
        for a, pa in plans.items():
            for b, pb in plans.items():
                if a < b:
                    assert not (at(pa, t) == at(pb, t + 1) and at(pb, t) == at(pa, t + 1) and at(pa, t) != at(pa, t + 1))


# ------------------------------------------------------- simulation / repair
def run(strategy, seed=0, n=15, **dis):
    dis = {**dict(obstacle_density=0.04, n_breakdowns=2, n_emergencies=2), **dis}
    cfg = ScenarioConfig.from_dict(dict(n_agents=n, repair=dict(strategy=strategy), disruptions=dis))
    sim = build(cfg, seed)
    sim.debug = True            # validate all future plans after every disruption
    sim.rt.strict = True        # and forbid reservation overwrites
    return sim, sim.run()


@pytest.mark.parametrize('strategy', ['local', 'solo', 'full'])
@pytest.mark.parametrize('seed', [0, 1, 2])
def test_run_is_safe_and_complete(strategy, seed):
    sim, m = run(strategy, seed)
    assert m['collisions'] == 0
    assert m['all_done']
    assert m['tasks_done'] + m['tasks_lost'] == m['tasks_total']


def test_no_disruption_matches_nominal():
    sim, m = run('local', 0, obstacle_density=0.0, n_breakdowns=0, n_emergencies=0)
    assert m['n_disruptions'] == 0 and m['soc'] == m['nominal_soc']


def test_temporary_blockage_changes_only_affected_robots():
    cfg = ScenarioConfig.from_dict(dict(n_agents=10, disruptions=dict(n_blockages=0, n_breakdowns=0, n_emergencies=0)))
    sim = build(cfg, 0)
    a = sim.agents[0]
    t = 5
    cell = a.path[t + 3]
    sim.events = [Event(t, 'blockage', 10, cell_prefs=[cell])]
    m = sim.run()
    rec = sim.records[0]
    assert 0 in rec.direct
    assert rec.altered == rec.direct         # nobody else had to change
    assert m['collisions'] == 0 and m['all_done']


def test_local_changes_fewer_plans_than_full_replan():
    _, ml = run('local', 4, n=25)
    _, mf = run('full', 4, n=25)
    assert ml['altered_mean'] < mf['altered_mean']


def test_permanent_breakdown_reassigns_tasks():
    cfg = ScenarioConfig.from_dict(dict(n_agents=10, disruptions=dict(n_blockages=0, n_breakdowns=0, n_emergencies=0)))
    sim = build(cfg, 1)
    sim.events = [Event(8, 'breakdown', INF, agent_prefs=[3])]
    m = sim.run()
    rec = sim.records[0]
    assert sim.agents[3].dead
    assert rec.reassigned and all(w != 3 for w in rec.reassigned.values())
    assert m['tasks_done'] + m['tasks_lost'] == m['tasks_total'] and m['collisions'] == 0
