"""Plan repair strategies.

LocalRepair (the proposed method), tiered so that the fewest agents are changed:
  Tier 1  solo replan: the disrupted agent replans its remaining goals with ST-A*,
          treating every other agent's reservations as fixed. Only it changes.
  Tier 2  negotiation: if the solo detour is too costly, the agent ALERTs neighbours
          within communication radius R, identifies the neighbours that block its
          ideal path, and asks the smallest subsets (1, 2, ... k) of them to replan
          jointly with it (it has priority). A subset is accepted only if its total
          delay plus a per-altered-agent penalty beats the solo detour; the search
          stops at the first subset size that achieves this.
          Chained negotiation (v2): a coalition member that cannot replan, or only
          with a delay above delta, may itself recruit up to `chain_fanout` of its
          own blockers within its own radius R, up to `chain_depth` hops. The whole
          tree is scored with the same rule, so a chain is only taken when it beats
          going solo and every flat coalition.
  Tier 3  the same negotiation with radius 2R.
  Tier 3b (v2, recorded as tier 6) local group replan: prioritized ST-A* for the
          stuck agent plus the movable robots within 2R whose plans cross its ideal
          corridor (at most `group_max`); accepted under the same score rule.
  Tier 4  fallback: stay in place this step (others that planned through this
          cell are repaired in cascade), retry next step.
The global planner is never invoked.

Emergency deadlines (v2): a plan that makes an emergency robot miss its delivery
deadline is charged `deadline_penalty` in every comparison, and emergency robots
may form coalitions of up to `emergency_max_set_size`.

LocalFlatRepair is the v1 method (no chains, no tier 3b, no deadline term).
SoloRepair is LocalFlatRepair with negotiation disabled (ablation).
FullReplan is the comparison baseline: prioritized planning for all agents from
the current state (i.e. re-invoking the MAPF planner from scratch).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
from typing import TYPE_CHECKING

from .agent import Goal
from .grid import INF, Cell
from .prioritized import PlanRequest, plan_prioritized
from .st_astar import st_astar

if TYPE_CHECKING:
    from .simulator import DisruptionRecord, Simulator


def pad(path: list[Cell], k: int) -> list[Cell]:
    return path if len(path) > k else path + [path[-1]] * (k + 1 - len(path))


@dataclass
class Candidate:
    members: tuple[int, ...]
    paths: dict[int, list[Cell]]      # full paths, including the disrupted agent
    delay: int
    score: float
    depth: int = 1                    # 1 = flat coalition, 2+ = chained


def goal_times(path: list[Cell], k: int, goals: list[Goal]) -> list[int | None]:
    """Time at which each goal is reached when following `path` from time k (goals in order)."""
    out: list[int | None] = [None] * len(goals)
    gi = 0
    for tau in range(k, len(path)):
        while gi < len(goals) - 1 and path[tau] == goals[gi].cell:
            out[gi] = tau
            gi += 1
        if gi == len(goals) - 1:
            break
    return out


class LocalRepair:
    negotiate = True
    chains = True           # v2: chained negotiation
    group = True            # v2: tier 3b local group replan
    deadlines = True        # v2: emergency deadline term in the repair score

    def __init__(self, sim: Simulator):
        self.sim = sim
        self.cfg = sim.rcfg
        self.queue: list[int] = []      # agents still awaiting repair (their plans are invalid)
        self.force_negotiate = False
        self._msgs = 0                  # messages sent inside a chained negotiation attempt
        self.hold_since: dict[int, int] = {}    # agent on hold -> time the hold started
        self._radius_max = 0            # widest radius allowed for the agent being repaired

    # ------------------------------------------------------------------ API
    def repair(self, queue: list[int], t: int, rec: DisruptionRecord) -> None:
        self.queue = queue = list(dict.fromkeys(queue))
        guard = 0
        while queue and guard < 20 * len(self.sim.agents):
            guard += 1
            aid = queue.pop(0)
            for n in self.repair_agent(aid, t, rec):
                if n not in queue:
                    queue.append(n)
        self.queue = []
        for aid in queue:       # safety net (never observed): hold and retry next step
            self.sim.guard_hits += 1
            ag = self.sim.agents[aid]
            k = max(t, ag.fixed_until)
            self.sim.rt.remove_path(aid, ag.path, k)
            self._commit(aid, pad(ag.path, k)[:k + 1], k)
            self.sim.pending[aid] = rec
            self.hold_since.setdefault(aid, t)

    def retry(self, aid: int, t: int, rec: DisruptionRecord) -> None:
        """Re-attempt repair for an agent on hold. Negotiation is always allowed here
        (whatever the strategy) because two holding agents can block each other and
        only a joint replan breaks such a deadlock."""
        self.force_negotiate = True
        try:
            LocalRepair.repair(self, [aid], t, rec)
        finally:
            self.force_negotiate = False

    # ------------------------------------------------------------ helpers
    def _neighbours(self, aid: int, t: int, radius: int) -> list[int]:
        sim = self.sim
        here = sim.agents[aid].pos(t)
        return [o.id for o in sim.agents
                if o.id != aid and sim.bus.in_range(here, o.pos(t), radius)]

    def _movable(self, oid: int, t: int) -> bool:
        o = self.sim.agents[oid]
        # Agents still in the repair queue hold invalid plans and cannot negotiate;
        # agents on hold (tier 4) have a valid plan and may be asked to move.
        return (not o.dead and o.fixed_until <= t and not o.is_emergency
                and oid not in self.queue)

    def _blockers(self, lb_path: list[Cell], k: int, aid: int) -> list[int]:
        """Agents whose reservations collide with the agent's ideal (agent-free) path."""
        rt = self.sim.rt
        first: dict[int, int] = {}
        for i, c in enumerate(lb_path):
            tau = k + i
            owner = rt.cell_res.get(c, {}).get(tau)
            if owner is None:
                p = rt.parked.get(c)
                if p is not None and tau >= p[1]:
                    owner = p[0]
            if owner is None and i > 0:
                owner = rt.edge_res.get((c, lb_path[i - 1], tau - 1))
            if owner is not None and owner != aid and owner not in first:
                first[owner] = tau
        return sorted(first, key=first.get)

    def _commit(self, aid: int, full: list[Cell], k: int) -> None:
        self.sim.agents[aid].path = full
        self.sim.rt.add_path(aid, full, k)

    def _deadline_cost(self, aid: int, full: list[Cell], k: int) -> float:
        """deadline_penalty for each emergency delivery `full` would make late."""
        sim = self.sim
        if not (self.deadlines and sim.deadlines and sim.agents[aid].is_emergency):
            return 0.0
        goals = sim.agents[aid].remaining_goals()
        cost = 0.0
        for g, tt in zip(goals, goal_times(full, k, goals)):
            dl = sim.deadlines.get(g.task_id) if g.kind == 'delivery' else None
            if dl is not None and (tt is None or tt > dl):
                cost += self.cfg.deadline_penalty
        return cost

    # ------------------------------------------------------- per-agent repair
    def repair_agent(self, aid: int, t: int, rec: DisruptionRecord) -> list[int]:
        # Expanding ring: a robot that has been on hold for a while widens its search,
        # because the robot it is waiting for may be out of range (deadlock breaker).
        cfg = self.cfg
        held = t - self.hold_since.get(aid, t)
        self._radius_max = 2 * cfg.comm_radius
        if held >= cfg.hold_patience:
            self._radius_max = min(2 * cfg.comm_radius * 2 ** (held // cfg.hold_patience),
                                   self.sim.grid.h + self.sim.grid.w)
        out = self._repair_agent(aid, t, rec)
        if aid not in self.sim.pending:
            self.hold_since.pop(aid, None)
        return out

    def _repair_agent(self, aid: int, t: int, rec: DisruptionRecord) -> list[int]:
        sim, cfg = self.sim, self.cfg
        ag = sim.agents[aid]
        sim.pending.pop(aid, None)
        if ag.dead:
            return []
        k = max(t, ag.fixed_until)
        base = pad(ag.path, k)
        sim.rt.remove_path(aid, ag.path, k)
        prefix, start = base[:k], base[k]
        goals = [g.cell for g in ag.remaining_goals()]

        lb = st_astar(sim.grid, start, k, goals, aid, None, sim.blk)
        solo = st_astar(sim.grid, start, k, goals, aid, sim.rt, sim.blk, self.cfg.max_expansions)
        solo_delay = (len(solo) - len(lb)) if (solo is not None and lb is not None) else INF
        threshold = cfg.delta_emergency if ag.is_emergency else cfg.delta
        # Deadline term: a solo plan is only "good enough" if it is no later than the
        # ideal path with respect to emergency deadlines.
        solo_dl = self._deadline_cost(aid, prefix + solo, k) if solo is not None else 0.0
        lb_dl = self._deadline_cost(aid, prefix + lb, k) if lb is not None else 0.0
        solo_score = solo_delay + solo_dl

        if solo is not None and solo_delay <= threshold and solo_dl <= lb_dl:
            self._commit(aid, prefix + solo, k)
            self._announce(aid, t, rec)
            rec.tiers[aid] = 1
            return []

        if (self.negotiate or self.force_negotiate) and lb is not None and k == t:
            if self._negotiate(aid, t, k, prefix, start, goals, lb, solo_score, rec):
                return []
            urgent = ag.is_emergency or solo is None
            if self.group and (urgent or cfg.group_scope == 'all') and self._group_replan(aid, t, k, prefix, start, goals, lb, solo_score, rec):
                return []

        if solo is not None:
            self._commit(aid, prefix + solo, k)
            self._announce(aid, t, rec)
            rec.tiers[aid] = 1
            return []

        # Tier 4: nothing feasible now -> hold position, retry next step.
        hold = base[:k + 1]
        self._commit(aid, hold, k)
        sim.pending[aid] = rec
        self.hold_since.setdefault(aid, t)
        rec.tiers[aid] = 4
        return [o for o in sim.rt.owners_at(start, k + 1) if o != aid]

    def _announce(self, aid: int, t: int, rec: DisruptionRecord) -> None:
        nb = self._neighbours(aid, t, self.cfg.comm_radius)
        self.sim.bus.broadcast(t, aid, nb, 'UPDATE')
        rec.messages += len(nb)

    # ------------------------------------------------------------ negotiation
    def _negotiate(self, aid, t, k, prefix, start, goals, lb, solo_delay, rec) -> bool:
        """`solo_delay` is the score of going solo (delay + deadline term)."""
        sim, cfg = self.sim, self.cfg
        bus = sim.bus
        max_size = cfg.max_set_size
        if self.deadlines and sim.agents[aid].is_emergency:
            max_size = max(max_size, cfg.emergency_max_set_size)
        rings = [(2, cfg.comm_radius), (3, 2 * cfg.comm_radius)]
        if self._radius_max > 2 * cfg.comm_radius:
            rings.append((3, self._radius_max))
        for tier, radius in rings:
            nbrs = self._neighbours(aid, t, radius)
            bus.broadcast(t, aid, nbrs, 'ALERT')
            for o in nbrs:
                bus.send(t, o, aid, 'STATUS')
            rec.messages += 2 * len(nbrs)
            nbr_set = {o for o in nbrs if self._movable(o, t)}
            if not nbr_set:
                continue
            # Only agents that actually block the ideal path can help by moving.
            cands = [b for b in self._blockers(lb, k, aid) if b in nbr_set][:max(cfg.max_candidates, max_size)]
            if not cands:
                continue

            # Smallest coalition first: stop at the first size that beats going solo.
            best: Candidate | None = None
            asked: set[int] = set()
            for size in range(1, min(max_size, len(cands)) + 1):
                if cfg.alter_penalty * size >= solo_delay:
                    break       # even a zero-delay coalition of this size cannot pay off
                for members in itertools.combinations(cands, size):
                    for m in members:
                        bus.send(t, aid, m, 'REQUEST')
                        bus.send(t, m, aid, 'PROPOSE')
                    rec.messages += 2 * size
                    asked.update(members)
                    self._msgs = 0
                    cand = self._try_joint(aid, t, k, prefix, start, goals, len(lb), members)
                    rec.messages += self._msgs
                    if cand is not None and (best is None or cand.score < best.score):
                        best = cand
                if best is not None and best.score < solo_delay:
                    break
            if best is not None and best.score < solo_delay:
                for m in best.members:
                    bus.send(t, aid, m, 'COMMIT')
                    bus.send(t, m, aid, 'ACCEPT')
                rejected = asked - set(best.members)
                for m in rejected:
                    bus.send(t, aid, m, 'REJECT')
                rec.messages += 2 * len(best.members) + len(rejected)
                for m in best.members:
                    sim.rt.remove_path(m, sim.agents[m].path, t)
                self._commit(aid, best.paths[aid], k)
                rec.tiers[aid] = tier
                rec.chain_depth = max(rec.chain_depth, best.depth)
                for m in best.members:
                    self._commit(m, best.paths[m], t)
                    rec.tiers.setdefault(m, tier)
                    sim.pending.pop(m, None)
                self._announce(aid, t, rec)
                return True
        return False

    def _try_joint(self, aid, t, k, prefix, start, goals, lb_len, members) -> Candidate | None:
        """Tentatively replan `aid` (first, highest priority) plus `members` (and, with
        chains, the robots they recruit); always restores rt."""
        sim = self.sim
        rt = sim.rt
        old = {m: sim.agents[m].path for m in members}
        for m in members:
            rt.remove_path(m, old[m], t)
        result = None
        pa = st_astar(sim.grid, start, k, goals, aid, rt, sim.blk, self.cfg.max_expansions)
        if pa is not None:
            full_a = prefix + pa
            rt.add_path(aid, full_a, k)
            ctx = dict(old=old, visited={aid, *members})
            opt = self._plan_group(list(members), t, 1, ctx)
            rt.remove_path(aid, full_a, k)
            if opt is not None:
                paths, delay, depth = opt
                delay += len(pa) - lb_len
                score = delay + self.cfg.alter_penalty * len(paths) + self._deadline_cost(aid, full_a, k)
                result = Candidate(tuple(paths), {aid: full_a, **paths}, delay, score, depth)
        for m in members:
            rt.add_path(m, old[m], t)
        return result

    def _plan_group(self, order: list[int], t: int, depth: int, ctx: dict):
        """Replan `order` one by one (old paths already removed from rt). A member that
        fails, or is delayed by more than delta, may recruit its own blockers (chains).
        Returns (paths of all altered robots, total delay, deepest level) or None.
        rt is left exactly as on entry."""
        sim, cfg = self.sim, self.cfg
        rt, old = sim.rt, ctx['old']
        visited0 = set(ctx['visited'])
        added: list[tuple[int, list[Cell]]] = []
        freed: list[int] = []
        paths: dict[int, list[Cell]] = {}
        delay, used, ok = 0, depth, True
        for m in order:
            base = pad(old[m], t)
            gm = [g.cell for g in sim.agents[m].remaining_goals()]
            pm = st_astar(sim.grid, base[t], t, gm, m, rt, sim.blk, cfg.max_expansions)
            best = None     # (score, paths, delay, depth, recruits)
            if pm is not None:
                d = (t + len(pm) - 1) - max(len(old[m]) - 1, t)
                best = (d, {m: base[:t] + pm}, d, depth, [])
            if self.chains and depth < cfg.chain_depth and (best is None or best[0] > cfg.delta):
                for opt in self._recruit(m, t, depth, ctx, base, gm, best[0] if best else INF):
                    if best is None or opt[0] < best[0]:
                        best = opt
            if best is None:
                ok = False
                break
            _, ps, dd, dep, recruits = best
            for r in recruits:
                rt.remove_path(r, old[r], t)
                freed.append(r)
                ctx['visited'].add(r)
            for a, p in ps.items():
                rt.add_path(a, p, t)
                added.append((a, p))
                paths[a] = p
            delay += dd
            used = max(used, dep)
        for a, p in reversed(added):
            rt.remove_path(a, p, t)
        for r in freed:
            rt.add_path(r, old[r], t)
        if not ok:
            ctx['visited'].clear()
            ctx['visited'].update(visited0)
            return None
        return paths, delay, used

    def _recruit(self, m: int, t: int, depth: int, ctx: dict, base: list[Cell], gm: list[Cell], bound: float):
        """Yield options in which `m` recruits 1..chain_fanout of its own blockers.
        Each option is (score, paths, delay, depth, recruits); score counts the recruits'
        alter penalty so that it is comparable with m replanning alone."""
        sim, cfg = self.sim, self.cfg
        rt, old, visited = sim.rt, ctx['old'], ctx['visited']
        # Relaxed path: what m could do if the movable robots in its range made way.
        # Those whose plans block that path are the ones worth recruiting.
        near = [o.id for o in sim.agents
                if o.id not in visited and self._movable(o.id, t)
                and sim.bus.in_range(base[t], o.pos(t), cfg.comm_radius)]
        for o in near:
            rt.remove_path(o, sim.agents[o].path, t)
        relaxed = st_astar(sim.grid, base[t], t, gm, m, rt, sim.blk, cfg.max_expansions)
        for o in near:
            rt.add_path(o, sim.agents[o].path, t)
        if relaxed is None:
            return
        near_set = set(near)
        cands = [b for b in self._blockers(relaxed, t, m) if b in near_set][:cfg.chain_fanout]
        budget = cfg.max_chain_nodes - (len(visited) - 1)       # visited includes the initiator
        for size in range(1, min(cfg.chain_fanout, len(cands)) + 1):
            if size > budget or cfg.alter_penalty * size >= bound:
                break
            for subs in itertools.combinations(cands, size):
                for r in subs:
                    sim.bus.send(t, m, r, 'REQUEST')
                    sim.bus.send(t, r, m, 'PROPOSE')
                self._msgs += 2 * size
                vis0 = set(visited)
                visited.update(subs)
                for r in subs:
                    old[r] = sim.agents[r].path
                    rt.remove_path(r, old[r], t)
                opt = self._plan_group([m, *subs], t, depth + 1, ctx)
                for r in subs:
                    rt.add_path(r, old[r], t)
                visited.clear()
                visited.update(vis0)
                if opt is not None:
                    paths, d, used = opt
                    recruits = [a for a in paths if a != m]
                    yield d + cfg.alter_penalty * len(recruits), paths, d, used, recruits

    # ------------------------------------------------- tier 3b: group replan
    def _group_replan(self, aid, t, k, prefix, start, goals, lb, solo_score, rec) -> bool:
        """Prioritized ST-A* for `aid` plus the movable robots within 2R whose plans cross
        its ideal corridor. Everyone else is a hard constraint. Never global."""
        sim, cfg = self.sim, self.cfg
        rt = sim.rt
        corridor = set(lb)
        horizon = t + len(lb) + cfg.delta
        group = []
        for o in self._neighbours(aid, t, self._radius_max):
            if not self._movable(o, t):
                continue
            p = sim.agents[o].path
            if any(p[min(tau, len(p) - 1)] in corridor for tau in range(t + 1, horizon + 1)):
                group.append(o)
        if not group:
            return False
        group.sort(key=lambda o: sim.grid.dist(start, sim.agents[o].pos(t)))
        group = group[:cfg.group_max]
        sim.bus.broadcast(t, aid, group, 'ALERT')
        rec.messages += len(group)

        old = {o: sim.agents[o].path for o in group}
        for o in group:
            rt.remove_path(o, old[o], t)
        reqs = [PlanRequest(aid, start, k, goals, prefix)]
        rest = []
        for o in group:
            b = pad(old[o], t)
            gm = [g.cell for g in sim.agents[o].remaining_goals()]
            rest.append(PlanRequest(o, b[t], t, gm, b[:t]))
        rest.sort(key=lambda r: -sum(sim.grid.dist(x, y) for x, y in zip([r.start] + r.goals, r.goals)))
        planned = plan_prioritized(sim.grid, rt, sim.blk, reqs + rest, np.random.default_rng(t),
                                   fixed_head=1, attempts=3, park_unplanned=False,
                                   max_expansions=cfg.max_expansions)
        accepted = False
        if planned is not None:
            full_a = planned[aid]
            delay = (len(full_a) - 1) - (k + len(lb) - 1)
            changed = []
            for o in group:
                new = planned[o]
                if any(new[min(i, len(new) - 1)] != old[o][min(i, len(old[o]) - 1)]
                       for i in range(t, max(len(new), len(old[o])))):
                    changed.append(o)
                    delay += (len(new) - 1) - max(len(old[o]) - 1, t)
            score = delay + cfg.alter_penalty * len(changed) + self._deadline_cost(aid, full_a, k)
            accepted = score < solo_score
            if not accepted:
                for r in reqs + rest:
                    rt.remove_path(r.aid, planned[r.aid], r.t0)
        if not accepted:
            for o in group:
                rt.add_path(o, old[o], t)
            return False
        # Unchanged group members got their old plan back; changed ones commit the new one.
        sim.agents[aid].path = planned[aid]
        rec.tiers[aid] = 6
        for o in group:
            sim.agents[o].path = planned[o]
            if o in changed:
                rec.tiers.setdefault(o, 6)
                sim.pending.pop(o, None)
                sim.bus.send(t, aid, o, 'COMMIT')
        rec.messages += len(changed)
        self._announce(aid, t, rec)
        return True


class LocalFlatRepair(LocalRepair):
    """The v1 method: flat coalitions only, no tier 3b, no deadline term."""
    chains = False
    group = False
    deadlines = False


class SoloRepair(LocalFlatRepair):
    negotiate = False


class FullReplan(LocalFlatRepair):
    """Baseline: re-invoke prioritized planning for every agent from the current state."""
    negotiate = False

    def repair(self, queue: list[int], t: int, rec: DisruptionRecord) -> None:
        sim = self.sim
        live = [a for a in sim.agents if not a.dead]
        saved = {a.id: (a.path, max(t, a.fixed_until)) for a in live}
        reqs = []
        for a in live:
            k = max(t, a.fixed_until)
            base = pad(a.path, k)
            sim.rt.remove_path(a.id, a.path, k)
            reqs.append((a, PlanRequest(a.id, base[k], k, [g.cell for g in a.remaining_goals()], base[:k])))
        heads = [r for a, r in reqs if a.is_emergency or a.fixed_until > t]
        rest = sorted((r for a, r in reqs if not (a.is_emergency or a.fixed_until > t)),
                      key=lambda r: -sum(sim.grid.dist(x, y) for x, y in zip([r.start] + r.goals, r.goals)))
        order = sorted(heads, key=lambda r: not sim.agents[r.aid].is_emergency) + rest
        sim.bus.broadcast(t, -1, [a.id for a in live], 'ALERT')
        rec.messages += len(live)
        planned = plan_prioritized(sim.grid, sim.rt, sim.blk, order, sim.rng, fixed_head=len(heads),
                                   attempts=5, park_unplanned=False, max_expansions=50_000)
        if planned is not None:
            for aid, full in planned.items():
                sim.agents[aid].path = full
                rec.tiers[aid] = 5
            sim.bus.broadcast(t, -1, list(planned), 'COMMIT')
            rec.messages += len(planned)
            return
        # Global replanning failed: restore the still-valid plans and fall back to
        # per-agent repair for the robots the disruption invalidated.
        for aid, (p, k) in saved.items():
            if aid not in queue:
                sim.rt.add_path(aid, p, k)
        LocalRepair.repair(self, queue, t, rec)



STRATEGIES = {'local': LocalRepair, 'local-flat': LocalFlatRepair, 'solo': SoloRepair, 'full': FullReplan}
