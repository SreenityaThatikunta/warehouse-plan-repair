from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field

import numpy as np

from .agent import Agent, Goal
from .comms import MessageBus
from .events import Event
from .grid import INF, Cell
from .prioritized import PlanRequest, plan_prioritized
from .repair import STRATEGIES, pad
from .st_astar import st_astar
from .reservation import Blockages, ReservationTable


@dataclass
class RepairConfig:
    strategy: str = 'local'
    comm_radius: int = 6
    delta: int = 4
    delta_emergency: int = 0
    alter_penalty: float = 3.0
    max_set_size: int = 3
    max_candidates: int = 4
    max_expansions: int = 60_000
    chain_depth: int = 2
    chain_fanout: int = 2
    max_chain_nodes: int = 6
    group_max: int = 8
    group_scope: str = 'urgent'
    emergency_slack: int = 3
    emergency_max_set_size: int = 4
    deadline_penalty: float = 10.0
    hold_patience: int = 10


@dataclass
class DisruptionRecord:
    id: int
    t: int
    kind: str
    permanent: bool
    duration: float
    cell: Cell | None = None
    agent: int | None = None
    direct: set = field(default_factory=set)
    altered: set = field(default_factory=set)
    tiers: dict = field(default_factory=dict)
    reassigned: dict = field(default_factory=dict)
    lost_tasks: list = field(default_factory=list)
    messages: int = 0
    chain_depth: int = 0
    cpu: float = 0.0
    old_paths: dict = field(default_factory=dict)
    new_paths: dict = field(default_factory=dict)

    @property
    def collateral(self) -> set:
        return self.altered - self.direct


def differs(p: list[Cell], q: list[Cell], t: int) -> bool:
    for i in range(t, max(len(p), len(q))):
        if p[min(i, len(p) - 1)] != q[min(i, len(q) - 1)]:
            return True
    return False


class Simulator:
    def __init__(self, warehouse, agents: list[Agent], events: list[Event], rcfg: RepairConfig,
                 seed: int = 0, n_tasks: int = 0):
        self.wh = warehouse
        self.grid = warehouse.grid
        self.agents = agents
        self.events = sorted(events, key=lambda e: e.t)
        self.rcfg = rcfg
        self.rng = np.random.default_rng(seed + 7919)
        self.rt = ReservationTable()
        self.blk = Blockages()
        self.bus = MessageBus()
        self.strategy = STRATEGIES[rcfg.strategy](self)
        self.pending: dict[int, DisruptionRecord] = {}
        self.records: list[DisruptionRecord] = []
        self.collisions: list[tuple] = []
        self.blockage_log: list[tuple[Cell, int, float]] = []
        self.n_tasks = n_tasks
        self.t = 0
        self.nominal: dict | None = None
        self.debug = False
        self.guard_hits = 0
        self.deadlines: dict[int, int] = {}
        self.rt.strict = False

    def initial_plan(self) -> bool:
        reqs = [PlanRequest(a.id, a.start, 0, [g.cell for g in a.goals], []) for a in self.agents]
        reqs.sort(key=lambda r: -sum(self.grid.dist(x, y) for x, y in zip([r.start] + r.goals, r.goals)))
        planned = plan_prioritized(self.grid, self.rt, None, reqs, np.random.default_rng(0), attempts=20)
        if planned is None:
            return False
        for a in self.agents:
            a.path = planned[a.id]
        self.nominal = self.plan_metrics()
        return True

    def plan_metrics(self) -> dict:
        soc = ms = soc_total = 0
        for a in self.agents:
            b = copy.deepcopy(a)
            for t in range(self.t, len(b.path)):
                b.update_progress(t)
            soc += b.last_delivery
            ms = max(ms, b.last_delivery)
            soc_total += b.end_time
        return dict(soc=soc, makespan=ms, soc_total=soc_total)

    def _live(self) -> list[Agent]:
        return [a for a in self.agents if not a.dead]

    def _remaining_goal_cells(self) -> set[Cell]:
        return {g.cell for a in self._live() for g in a.remaining_goals()}

    def _connected_without(self, extra: set[Cell]) -> bool:
        blocked = self.blk.permanent_cells() | {a.pos(self.t) for a in self.agents if a.dead} | extra
        live = [a for a in self._live() if a.pos(self.t) not in blocked]
        if not live:
            return True
        reach = self.grid.reachable(live[0].pos(self.t), blocked)
        need = {a.pos(self.t) for a in live} | self._remaining_goal_cells()
        return need <= reach

    def _usable_cell(self, cell: Cell, alternatives: list[Cell], src: Cell) -> Cell | None:
        blocked = self.blk.permanent_cells() | {a.pos(self.t) for a in self.agents if a.dead}
        reach = self.grid.reachable(src, blocked)
        if cell in reach:
            return cell
        ok = [c for c in alternatives if c in reach]
        return min(ok, key=lambda c: abs(c[0] - cell[0]) + abs(c[1] - cell[1])) if ok else None

    def _pick_blockage_cell(self, ev: Event, t: int) -> Cell | None:
        occupied = {a.pos(t) for a in self.agents}
        taboo = occupied | set(self.blk.active_at(t)) | self.blk.permanent_cells() \
            | set(self.wh.homes) | set(self.wh.stations)
        if ev.safe:
            taboo |= self._remaining_goal_cells()
        soon = None
        if ev.on_path:
            soon = {a.pos(tau) for a in self._live() for tau in range(t + 2, t + 12)}
        for c in ev.cell_prefs:
            if c in taboo or (soon is not None and c not in soon):
                continue
            if ev.safe and ev.permanent and not self._connected_without({c}):
                continue
            return c
        return None

    def _pick_agent(self, ev: Event, t: int) -> Agent | None:
        goal_cells = None
        for aid in ev.agent_prefs:
            a = self.agents[aid]
            if a.dead or a.done(t) or a.fixed_until > t:
                continue
            if ev.kind == 'breakdown' and ev.permanent and ev.safe:
                if goal_cells is None:
                    goal_cells = {g.cell for o in self._live() if o.id != aid for g in o.remaining_goals()}
                if a.pos(t) in goal_cells or not self._connected_without({a.pos(t)}):
                    continue
            return a
        return None

    def handle_event(self, ev: Event, t: int) -> DisruptionRecord | None:
        t0 = time.perf_counter()
        snapshot = {a.id: a.path for a in self.agents}
        rec = DisruptionRecord(len(self.records), t, ev.kind, ev.permanent, ev.duration)

        if ev.kind == 'blockage':
            c = self._pick_blockage_cell(ev, t)
            if c is None:
                return None
            rec.cell = c
            end = t + ev.duration if not ev.permanent else INF
            owners = self.rt.owners_at(c, t, end - 1)
            self.blk.add(c, t, end)
            self.blockage_log.append((c, t, end))
            queue = sorted(owners, key=owners.get)
            rec.direct = set(owners)

        elif ev.kind == 'breakdown':
            a = self._pick_agent(ev, t)
            if a is None:
                return None
            rec.agent, rec.cell = a.id, a.pos(t)
            c = a.pos(t)
            if ev.permanent:
                owners = {o: tt for o, tt in self.rt.owners_at(c, t + 1).items() if o != a.id}
                self.rt.remove_path(a.id, a.path, t)
                a.path = pad(a.path, t)[:t + 1]
                self.rt.add_path(a.id, a.path, t)
                a.dead = True
                winners = self._reallocate(a, t, rec)
                queue = sorted(owners, key=owners.get) + [w for w in winners if w not in owners]
                rec.direct = {a.id} | set(owners) | set(winners)
            else:
                d = int(ev.duration)
                owners = {o: tt for o, tt in self.rt.owners_at(c, t + 1, t + d).items() if o != a.id}
                for o in owners:
                    self.rt.remove_path(o, self.agents[o].path, t)
                self.rt.remove_path(a.id, a.path, t)
                a.path = pad(a.path, t)[:t + 1] + [c] * d
                a.fixed_until = t + d
                self.rt.add_path(a.id, a.path, t)
                queue = [a.id] + sorted(owners, key=owners.get)
                rec.direct = {a.id} | set(owners)

        elif ev.kind == 'emergency':
            a = self._pick_agent(ev, t)
            if a is None:
                return None
            ev = copy.copy(ev)
            if ev.safe:
                ev.pickup = self._usable_cell(ev.pickup, self.wh.pickups, a.pos(t))
                ev.delivery = self._usable_cell(ev.delivery, self.wh.stations, a.pos(t))
            if ev.pickup is None or ev.delivery is None:
                return None
            rec.agent, rec.cell = a.id, ev.pickup
            tid = self.n_tasks
            self.n_tasks += 1
            a.goals[a.ptr:a.ptr] = [Goal(ev.pickup, 'pickup', tid), Goal(ev.delivery, 'delivery', tid)]
            a.emergency_tasks.add(tid)
            k = max(t, a.fixed_until)
            ideal = st_astar(self.grid, pad(a.path, k)[k], k, [ev.pickup, ev.delivery], a.id, None, self.blk)
            if ideal is not None:
                self.deadlines[tid] = k + len(ideal) - 1 + self.rcfg.emergency_slack
            queue = [a.id]
            rec.direct = {a.id}
        else:
            raise ValueError(ev.kind)

        self.records.append(rec)
        self.strategy.repair(queue, t, rec)
        self._finalize(rec, snapshot, t)
        rec.cpu += time.perf_counter() - t0
        if self.debug:
            bad = self.validate_plans(t)
            if bad:
                raise AssertionError(f'plan conflicts after disruption {rec.id} ({ev.kind}) at t={t}: {bad[:5]}')
        return rec

    def _finalize(self, rec: DisruptionRecord, snapshot: dict, t: int) -> None:
        for a in self.agents:
            old = snapshot[a.id]
            if old is not a.path and differs(old, a.path, t):
                rec.altered.add(a.id)
                rec.old_paths.setdefault(a.id, list(old))
                rec.new_paths[a.id] = list(a.path)

    def _reallocate(self, dead: Agent, t: int, rec: DisruptionRecord) -> list[int]:
        here = dead.pos(t)
        blocked = self.blk.permanent_cells() | {a.pos(t) for a in self.agents if a.dead}
        homes, stations = set(self.wh.homes), set(self.wh.stations)
        pending: dict[int, list[Goal]] = {}
        for g in dead.remaining_goals():
            if g.kind != 'home':
                pending.setdefault(g.task_id, []).append(g)
        winners = []
        for tid, gs in pending.items():
            delivery = next(g.cell for g in gs if g.kind == 'delivery')
            if any(g.kind == 'pickup' for g in gs):
                pickup = next(g.cell for g in gs if g.kind == 'pickup')
            else:
                live = [o for o in self._live()]
                reach = self.grid.reachable(live[0].pos(t), blocked) if live else set()
                spots = [n for n in self.grid.neighbors(here)
                         if n in reach and n not in homes and n not in stations]
                if not spots:
                    rec.lost_tasks.append(tid)
                    continue
                pickup = spots[0]
            radius = self.rcfg.comm_radius
            bidders: list[Agent] = []
            while not bidders and radius <= self.grid.h + self.grid.w:
                bidders = [o for o in self._live() if self.bus.in_range(here, o.pos(t), radius)]
                radius *= 2
            if not bidders:
                continue
            self.bus.broadcast(t, dead.id, [o.id for o in bidders], 'TASK_ANNOUNCE')
            rec.messages += 2 * len(bidders) + 1

            def bid(o: Agent) -> int:
                prev = o.goals[-2].cell if o.ptr < len(o.goals) - 1 else o.pos(t)
                home = o.goals[-1].cell
                g = self.grid.dist
                return g(prev, pickup) + g(pickup, delivery) + g(delivery, home) - g(prev, home)

            for o in bidders:
                self.bus.send(t, o.id, dead.id, 'BID')
            win = min(bidders, key=lambda o: (bid(o), o.id))
            self.bus.send(t, dead.id, win.id, 'AWARD')
            win.goals[-1:-1] = [Goal(pickup, 'pickup', tid), Goal(delivery, 'delivery', tid)]
            if tid in dead.emergency_tasks:
                win.emergency_tasks.add(tid)
            rec.reassigned[tid] = win.id
            if win.id not in winners:
                winners.append(win.id)
        dead.goals = dead.goals[:dead.ptr] + [dead.goals[-1]]
        dead.emergency_tasks.clear()
        return winners

    def validate_plans(self, t: int) -> list[tuple]:
        horizon = max(len(a.path) for a in self.agents) + 1
        conflicts = []
        for tau in range(t, horizon):
            seen: dict[Cell, int] = {}
            for a in self.agents:
                c = a.pos(tau)
                if c in seen:
                    conflicts.append(('vertex', tau, seen[c], a.id, c))
                seen[c] = a.id
                if tau > t and self.blk.is_blocked(c, tau):
                    conflicts.append(('blocked', tau, a.id, c))
            nxt = {a.pos(tau + 1): a.id for a in self.agents}
            for a in self.agents:
                o = nxt.get(a.pos(tau))
                if o is not None and o != a.id and self.agents[o].pos(tau) == a.pos(tau + 1) \
                        and a.pos(tau) != a.pos(tau + 1) and a.id < o:
                    conflicts.append(('edge', tau, a.id, o))
        return conflicts

    def _check_step(self, t: int) -> None:
        pos_now = {a.id: a.pos(t) for a in self.agents}
        pos_next = {a.id: a.pos(t + 1) for a in self.agents}
        seen: dict[Cell, int] = {}
        for aid, c in pos_next.items():
            if c in seen:
                self.collisions.append(('vertex', t + 1, seen[c], aid, c))
            seen[c] = aid
            if self.grid.obs[c] or self.blk.is_blocked(c, t + 1):
                self.collisions.append(('obstacle', t + 1, aid, c))
        for a, b in ((a, b) for a in pos_now for b in pos_now if a < b):
            if pos_now[a] == pos_next[b] and pos_now[b] == pos_next[a] and pos_now[a] != pos_now[b]:
                self.collisions.append(('edge', t, a, b))

    stall_limit = 150

    def run(self, max_steps: int | None = None) -> dict:
        if not self.agents[0].path and not self.initial_plan():
            raise RuntimeError('initial planning failed')
        max_steps = max_steps or 4 * self.nominal['makespan'] + 400
        ev_i = 0
        for a in self.agents:
            a.update_progress(0)
        self.stalled = False
        last_progress = 0
        for t in range(max_steps):
            self.t = t
            while ev_i < len(self.events) and self.events[ev_i].t <= t:
                self.handle_event(self.events[ev_i], t)
                ev_i += 1
            for aid, rec in list(self.pending.items()):
                if aid in self.pending:
                    t0 = time.perf_counter()
                    snap = {a.id: a.path for a in self.agents}
                    self.strategy.retry(aid, t, rec)
                    self._finalize(rec, snap, t)
                    rec.cpu += time.perf_counter() - t0
            for a in self.agents:
                a.update_progress(t)
            if not self.pending and all(a.done(t) for a in self.agents):
                break
            self._check_step(t)
            ptrs = [a.ptr for a in self.agents]
            for a in self.agents:
                a.update_progress(t + 1)
            # stop once nothing has moved for stall_limit steps
            if any(a.pos(t) != a.pos(t + 1) for a in self.agents) or ptrs != [a.ptr for a in self.agents]:
                last_progress = t
            elif t - last_progress >= self.stall_limit:
                self.stalled = True
                break
        return self.metrics()

    def stuck_robots(self) -> dict[int, str]:
        t = self.t
        walls = self.blk.permanent_cells() | {a.pos(t) for a in self.agents if a.dead}
        out = {}
        for a in self._live():
            if a.done(t):
                continue
            reach = self.grid.reachable(a.pos(t), walls - {a.pos(t)})
            out[a.id] = 'unreachable' if any(g.cell not in reach for g in a.remaining_goals()) else 'deadlock'
        return out

    def metrics(self) -> dict:
        recs = self.records
        live = self._live()
        done_tasks = sum(len(a.completed) for a in self.agents)
        m = dict(
            soc=sum(a.last_delivery for a in self.agents),
            makespan=max(a.last_delivery for a in self.agents),
            soc_total=sum(a.end_time for a in live),
            steps=self.t,
            nominal_soc=self.nominal['soc'],
            nominal_makespan=self.nominal['makespan'],
            tasks_done=done_tasks,
            tasks_total=self.n_tasks,
            tasks_lost=sum(len(r.lost_tasks) for r in recs),
            all_done=all(a.done(self.t) for a in self.agents) and not self.pending,
            collisions=len(self.collisions),
            guard_hits=self.guard_hits,
            n_disruptions=len(recs),
            messages=self.bus.total,
        )
        m['soc_overhead'] = m['soc'] - m['nominal_soc']
        m['throughput'] = done_tasks / max(1, m['makespan'])
        m['completion'] = done_tasks / max(1, self.n_tasks)
        m['stalled'] = bool(getattr(self, 'stalled', False))
        stuck = self.stuck_robots() if not m['all_done'] else {}
        m['stuck_unreachable'] = sum(1 for v in stuck.values() if v == 'unreachable')
        m['stuck_deadlock'] = sum(1 for v in stuck.values() if v == 'deadlock')
        finish = {tid: tt for a in self.agents for tid, tt in a.completed}
        if self.deadlines:
            late = [max(0, finish[tid] - dl) for tid, dl in self.deadlines.items() if tid in finish]
            m['emergency_n'] = len(self.deadlines)
            m['emergency_on_time'] = sum(1 for tid, dl in self.deadlines.items()
                                         if finish.get(tid, INF) <= dl) / len(self.deadlines)
            m['emergency_lateness'] = float(np.mean(late)) if late else 0.0
        if recs:
            m['altered_mean'] = float(np.mean([len(r.altered) for r in recs]))
            m['collateral_mean'] = float(np.mean([len(r.collateral) for r in recs]))
            m['direct_mean'] = float(np.mean([len(r.direct) for r in recs]))
            m['repair_ms_mean'] = 1000 * float(np.mean([r.cpu for r in recs]))
            m['msgs_per_disruption'] = float(np.mean([r.messages for r in recs]))
            tiers = [tr for r in recs for tr in r.tiers.values()]
            for k in (1, 2, 3, 4, 5, 6):
                m[f'tier{k}'] = tiers.count(k)
            m['chained'] = sum(1 for r in recs if r.chain_depth >= 2)
        return m
