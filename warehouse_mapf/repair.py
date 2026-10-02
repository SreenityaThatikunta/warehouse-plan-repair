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
  Tier 3  the same negotiation with radius 2R.
  Tier 4  fallback: stay in place this step (others that planned through this
          cell are repaired in cascade), retry next step.
The global planner is never invoked.

SoloRepair is LocalRepair with negotiation disabled (ablation).
FullReplan is the comparison baseline: prioritized planning for all agents from
the current state (i.e. re-invoking the MAPF planner from scratch).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

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


class LocalRepair:
    negotiate = True

    def __init__(self, sim: Simulator):
        self.sim = sim
        self.cfg = sim.rcfg
        self.queue: list[int] = []      # agents still awaiting repair (their plans are invalid)
        self.force_negotiate = False

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

    # ------------------------------------------------------- per-agent repair
    def repair_agent(self, aid: int, t: int, rec: DisruptionRecord) -> list[int]:
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

        if solo is not None and solo_delay <= threshold:
            self._commit(aid, prefix + solo, k)
            self._announce(aid, t, rec)
            rec.tiers[aid] = 1
            return []

        if (self.negotiate or self.force_negotiate) and lb is not None and k == t:
            if self._negotiate(aid, t, k, prefix, start, goals, lb, solo_delay, rec):
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
        rec.tiers[aid] = 4
        return [o for o in sim.rt.owners_at(start, k + 1) if o != aid]

    def _announce(self, aid: int, t: int, rec: DisruptionRecord) -> None:
        nb = self._neighbours(aid, t, self.cfg.comm_radius)
        self.sim.bus.broadcast(t, aid, nb, 'UPDATE')
        rec.messages += len(nb)

    # ------------------------------------------------------------ negotiation
    def _negotiate(self, aid, t, k, prefix, start, goals, lb, solo_delay, rec) -> bool:
        sim, cfg = self.sim, self.cfg
        bus = sim.bus
        for tier, radius in ((2, cfg.comm_radius), (3, 2 * cfg.comm_radius)):
            nbrs = self._neighbours(aid, t, radius)
            bus.broadcast(t, aid, nbrs, 'ALERT')
            for o in nbrs:
                bus.send(t, o, aid, 'STATUS')
            rec.messages += 2 * len(nbrs)
            nbr_set = {o for o in nbrs if self._movable(o, t)}
            if not nbr_set:
                continue
            # Only agents that actually block the ideal path can help by moving.
            cands = [b for b in self._blockers(lb, k, aid) if b in nbr_set][:cfg.max_candidates]
            if not cands:
                continue

            # Smallest coalition first: stop at the first size that beats going solo.
            best: Candidate | None = None
            asked: set[int] = set()
            for size in range(1, min(cfg.max_set_size, len(cands)) + 1):
                if cfg.alter_penalty * size >= solo_delay:
                    break       # even a zero-delay coalition of this size cannot pay off
                for members in itertools.combinations(cands, size):
                    for m in members:
                        bus.send(t, aid, m, 'REQUEST')
                        bus.send(t, m, aid, 'PROPOSE')
                    rec.messages += 2 * size
                    asked.update(members)
                    cand = self._try_joint(aid, t, k, prefix, start, goals, len(lb), members)
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
                for m in best.members:
                    self._commit(m, best.paths[m], t)
                    rec.tiers.setdefault(m, tier)
                    sim.pending.pop(m, None)
                self._announce(aid, t, rec)
                return True
        return False

    def _try_joint(self, aid, t, k, prefix, start, goals, lb_len, members) -> Candidate | None:
        """Tentatively replan `aid` (first, highest priority) plus `members`; always restores rt."""
        sim = self.sim
        rt = sim.rt
        old = {m: sim.agents[m].path for m in members}
        for m in members:
            rt.remove_path(m, old[m], t)
        added: dict[int, tuple[list[Cell], int]] = {}
        result = None
        pa = st_astar(sim.grid, start, k, goals, aid, rt, sim.blk, self.cfg.max_expansions)
        if pa is not None:
            full_a = prefix + pa
            rt.add_path(aid, full_a, k)
            added[aid] = (full_a, k)
            delay = len(pa) - lb_len
            ok = True
            for m in members:
                base = pad(old[m], t)
                gm = [g.cell for g in sim.agents[m].remaining_goals()]
                pm = st_astar(sim.grid, base[t], t, gm, m, rt, sim.blk, self.cfg.max_expansions)
                if pm is None:
                    ok = False
                    break
                full_m = base[:t] + pm
                rt.add_path(m, full_m, t)
                added[m] = (full_m, t)
                delay += (len(full_m) - 1) - max(len(old[m]) - 1, t)
            if ok:
                result = Candidate(tuple(members), {a: p for a, (p, _) in added.items()}, delay,
                                   delay + self.cfg.alter_penalty * len(members))
        for a, (p, frm) in added.items():
            rt.remove_path(a, p, frm)
        for m in members:
            rt.add_path(m, old[m], t)
        return result


class SoloRepair(LocalRepair):
    negotiate = False


class FullReplan(LocalRepair):
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



STRATEGIES = {'local': LocalRepair, 'solo': SoloRepair, 'full': FullReplan}
