"""Matplotlib rendering of the warehouse: disruption snapshots (before / after repair) and GIF replay."""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib

if os.environ.get('WAREHOUSE_LIVE') != '1':     # set by `run --live` to keep an interactive backend
    matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FFMpegWriter, FuncAnimation, PillowWriter  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402

# Semantic colours (state, not identity).
C = dict(
    floor='#f7f6f2', grid='#e4e1d8', shelf='#c9c5b9', shelf_edge='#aaa69a', rack='#b8b4a7',
    station='#1baf7a', home='#e2dfd5', pickup='#f0eee8', agent='#2a78d6', altered='#eb6834',
    disrupted='#e34948', blocked='#2b2b29', emergency='#eda100', old='#8a8983', text='#0b0b0b',
    muted='#52514e',
)
_SHELF_CACHE: dict = {}


def _tile(ax, cell, color, z=1, inset=0.07, round_=0.18, **kw):
    r, c = cell
    ax.add_patch(FancyBboxPatch((c - 0.5 + inset, r - 0.5 + inset), 1 - 2 * inset, 1 - 2 * inset,
                                boxstyle=f'round,pad=0,rounding_size={round_}', facecolor=color,
                                edgecolor='none', zorder=z, **kw))


def _cell_rect(ax, cell, color, z=1, **kw):
    _tile(ax, cell, color, z=z, **kw)


def _shelf_blocks(wh) -> list[tuple[int, int, int, int]]:
    """Shelf cells grouped into rectangular blocks (r0, c0, r1, c1), cached per map."""
    key = id(wh)
    if key not in _SHELF_CACHE:
        cells, blocks = set(wh.shelves), []
        while cells:
            stack = [cells.pop()]
            comp = list(stack)
            while stack:
                r, c = stack.pop()
                for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                    if n in cells:
                        cells.remove(n)
                        stack.append(n)
                        comp.append(n)
            rs, cs = [x for x, _ in comp], [y for _, y in comp]
            blocks.append((min(rs), min(cs), max(rs), max(cs)))
        _SHELF_CACHE[key] = blocks
    return _SHELF_CACHE[key]


def draw_map(ax, sim) -> None:
    g, wh = sim.grid, sim.wh
    ax.set_facecolor(C['floor'])
    ax.set_xlim(-0.5, g.w - 0.5)
    ax.set_ylim(g.h - 0.5, -0.5)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(C['grid'])
        s.set_linewidth(1.2)
    for c in wh.pickups:
        _tile(ax, c, C['pickup'], z=0.5, inset=0.04, round_=0.12)
    for c in wh.homes:
        _tile(ax, c, C['home'], z=0.5, inset=0.12, round_=0.2)
    for c in wh.stations:
        _tile(ax, c, C['station'], z=0.6, inset=0.08, round_=0.22)
        r, cc = c
        ax.plot([cc], [r], marker='v', ms=3.2, color='white', zorder=0.7)
    for r0, c0, r1, c1 in _shelf_blocks(wh):
        ax.add_patch(FancyBboxPatch((c0 - 0.42, r0 - 0.42), c1 - c0 + 0.84, r1 - r0 + 0.84,
                                    boxstyle='round,pad=0,rounding_size=0.28', facecolor=C['shelf'],
                                    edgecolor=C['shelf_edge'], linewidth=0.8, zorder=1))
        for rr in range(r0, r1 + 1):        # rack line along each shelf row
            ax.plot([c0 - 0.2, c1 + 0.2], [rr, rr], color=C['rack'], lw=1.0, zorder=1.1,
                    solid_capstyle='round')


def draw_blocked(ax, sim, t: int) -> None:
    for cell, s, e in sim.blockage_log:
        if s <= t < e:
            _tile(ax, cell, C['blocked'], z=2, inset=0.06, round_=0.2)
            r, c = cell
            for dx in (1, -1):
                ax.plot([c - 0.25, c + 0.25], [r - 0.25 * dx, r + 0.25 * dx], color=C['disrupted'], lw=1.8,
                        zorder=3, solid_capstyle='round')


def is_broken(sim, a, t: int) -> bool:
    return any(r.kind == 'breakdown' and r.agent == a.id and r.t <= t < r.t + r.duration
               for r in sim.records)


def draw_agents(ax, sim, t: int, highlight: set | None = None, disrupted: set | None = None,
                labels: bool = True, size: float = 105) -> None:
    highlight = highlight or set()
    disrupted = disrupted or set()
    for a in sim.agents:
        r, c = a.pos(t)
        broken = is_broken(sim, a, t)
        color = C['disrupted'] if (a.id in disrupted or broken) else \
            C['altered'] if a.id in highlight else C['agent']
        ax.scatter([c + 0.06], [r + 0.08], s=size * 1.25, color='black', alpha=0.12, linewidth=0, zorder=5.9)
        ax.scatter([c], [r], s=size * 1.25, color=color, edgecolor='white', linewidth=1.5, zorder=6)
        if broken:
            ax.scatter([c], [r], s=size * 0.55, marker='x', color='white', linewidth=1.8, zorder=7)
        elif labels:
            ax.text(c, r, str(a.id), ha='center', va='center', fontsize=6.5, color='white',
                    zorder=7, fontweight='bold')


def draw_path(ax, path, t: int, horizon: int, color, style='-', lw=1.8, alpha=1.0, z=4) -> None:
    seg = [path[min(i, len(path) - 1)] for i in range(t, min(t + horizon, max(len(path), t + 1)))]
    if len(seg) < 2:
        return
    xs, ys = [c for _, c in seg], [r for r, _ in seg]
    casing = [pe.Stroke(linewidth=lw + 2.2, foreground='white', alpha=0.85 * alpha), pe.Normal()]
    ax.plot(xs, ys, style, color=color, lw=lw, alpha=alpha, zorder=z, solid_capstyle='round',
            solid_joinstyle='round', dash_capstyle='round', path_effects=casing if style == '-' else None)



def _legend(ax, extra=()):
    items = [
        Line2D([], [], marker='o', ls='', color=C['agent'], label='robot'),
        Line2D([], [], marker='o', ls='', color=C['altered'], label='plan altered'),
        Line2D([], [], marker='o', ls='', color=C['disrupted'], label='disrupted / broken'),
        Line2D([], [], marker='s', ls='', color=C['blocked'], label='blocked cell'),
        Line2D([], [], marker='s', ls='', color=C['shelf'], mec=C['shelf_edge'], label='shelf'),
        Line2D([], [], marker='s', ls='', color=C['station'], label='delivery station'),
        Line2D([], [], marker='s', ls='', color=C['home'], mec=C['shelf_edge'], label='dock'),
        *extra,
    ]
    ax.legend(handles=items, loc='upper center', bbox_to_anchor=(0.5, -0.02), ncol=5, fontsize=7,
              frameon=False)


def _event_marker(ax, rec) -> None:
    if rec.cell is None:
        return
    r, c = rec.cell
    ax.scatter([c], [r], s=420, facecolor='none', edgecolor=C['disrupted'], linewidth=2, zorder=8)
    if rec.kind == 'emergency':
        ax.scatter([c], [r], s=160, marker='*', color=C['emergency'], edgecolor=C['text'], lw=0.5, zorder=8)


def describe(rec) -> str:
    if rec.kind == 'blockage':
        what = f'cell {rec.cell} blocked ' + ('permanently' if rec.permanent else f'for {int(rec.duration)} steps')
    elif rec.kind == 'breakdown':
        what = f'robot {rec.agent} breaks down ' + ('permanently' if rec.permanent else f'for {int(rec.duration)} steps')
    else:
        what = f'robot {rec.agent} gets emergency task (pickup {rec.cell})'
    return f'Disruption #{rec.id} at t={rec.t}: {what}'


def save_disruption_snapshot(sim, rec, path: Path, horizon: int = 30) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.9), dpi=130)
    t = rec.t
    disrupted = {rec.agent} if rec.agent is not None else set()
    for ax, title, which in ((axes[0], 'Before repair: original plans', 'old'),
                             (axes[1], 'After repair: negotiated plans', 'new')):
        draw_map(ax, sim)
        draw_blocked(ax, sim, t if which == 'new' else t - 1)
        for aid in rec.altered:
            if which == 'old':
                draw_path(ax, rec.old_paths[aid], t, horizon, C['old'], '--', lw=1.6)
            else:
                draw_path(ax, rec.old_paths[aid], t, horizon, C['old'], '--', lw=1.0, alpha=0.45)
                draw_path(ax, rec.new_paths[aid], t, horizon, C['altered'], '-', lw=2.0)
        draw_agents(ax, sim, t, highlight=rec.altered if which == 'new' else set(), disrupted=disrupted)
        if which == 'new':
            _event_marker(ax, rec)
        ax.set_title(title, fontsize=10, color=C['text'], loc='left')
    fig.suptitle(f'{describe(rec)}\nagents directly affected: {len(rec.direct)}   '
                 f'plans altered: {len(rec.altered)}   additional (negotiated) changes: {len(rec.collateral)}   '
                 f'tiers used: {sorted(set(rec.tiers.values())) or "-"}',
                 fontsize=10, color=C['text'], x=0.01, ha='left')
    _legend(axes[1], extra=[Line2D([], [], ls='--', color=C['old'], label='original path'),
                            Line2D([], [], ls='-', color=C['altered'], label='repaired path')])
    fig.tight_layout()
    fig.savefig(path, facecolor='white')
    plt.close(fig)


def save_disruption_snapshots(sim, out: Path, max_n: int = 12) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    recs = sorted(sim.records, key=lambda r: -len(r.altered))[:max_n]
    for rec in sorted(recs, key=lambda r: r.id):
        if not rec.altered:
            continue
        p = out / f'disruption_{rec.id:02d}_{rec.kind}.png'
        save_disruption_snapshot(sim, rec, p)
        paths.append(p)
    return paths


def draw_frame(ax, sim, t: int, trail: int = 6, recent_window: int = 8) -> None:
    """One animation frame: map, blockages, robots with short trails, and for 8 steps after each
    disruption the robots whose plans changed (orange) with their repaired paths."""
    ax.clear()
    draw_map(ax, sim)
    draw_blocked(ax, sim, t)
    active = [r for r in sim.records if r.t <= t < r.t + recent_window]
    hl, dis = set(), set()
    for r in active:
        hl |= r.altered
        if r.agent is not None:
            dis.add(r.agent)
        _event_marker(ax, r)
        for aid in r.altered:
            draw_path(ax, r.new_paths[aid], t, 20, C['altered'], '-', lw=1.4, alpha=0.8)
    for a in sim.agents:
        seg = [a.pos(tau) for tau in range(max(0, t - trail), t + 1)]
        ax.plot([c for _, c in seg], [r for r, _ in seg], color=C['agent'], alpha=0.25, lw=2, zorder=3)
    draw_agents(ax, sim, t, highlight=hl, disrupted=dis, size=105)
    done = sum(1 for a in sim.agents for (_, tt) in a.completed if tt <= t)
    head = f't = {t:>3}   tasks delivered: {done}/{sim.n_tasks}   strategy: {sim.rcfg.strategy}'
    if active:
        head += '\n' + describe(active[-1]) + f'  ->  {len(active[-1].altered)} plan(s) altered'
    ax.set_title(head, fontsize=9, loc='left', color=C['text'])


def _animation(sim, fps: int, trail: int, dpi: int, repeat: bool = True):
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=dpi)
    anim = FuncAnimation(fig, lambda t: draw_frame(ax, sim, t, trail), frames=range(sim.t + 1),
                         interval=1000 // fps, repeat=repeat)
    return fig, anim


def save_demo_frame(sim, t: int, path: Path) -> None:
    """A single animation frame with a legend (for the report)."""
    fig, ax = plt.subplots(figsize=(10, 6.6), dpi=150)
    draw_frame(ax, sim, t)
    _legend(ax, extra=(Line2D([], [], color=C['altered'], lw=1.6, label='repaired path'),
                       Line2D([], [], color=C['agent'], lw=2, alpha=0.35, label='recent trail'),
                       Line2D([], [], marker='o', ls='', mfc='none', mec=C['disrupted'], ms=9,
                              label='disruption location'),
                       Line2D([], [], marker='s', ls='', color=C['pickup'], mec=C['grid'], label='pickup cell')))
    fig.tight_layout()
    fig.savefig(path, facecolor='white')
    plt.close(fig)


def save_animation(sim, path: Path, fps: int = 6, trail: int = 6, dpi: int = 90) -> None:
    """MP4 (H.264, needs ffmpeg) or GIF, chosen by the file extension."""
    fig, anim = _animation(sim, fps, trail, dpi)
    if Path(path).suffix == '.mp4':
        writer = FFMpegWriter(fps=fps, codec='libx264', extra_args=['-pix_fmt', 'yuv420p'])
    else:
        writer = PillowWriter(fps=fps)
    anim.save(path, writer=writer)
    plt.close(fig)


def show_live(sim, fps: int = 8) -> None:
    """Play the finished run in an interactive matplotlib window."""
    fig, anim = _animation(sim, fps, trail=6, dpi=100, repeat=False)
    plt.show()


def save_frame(sim, t: int, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=130)
    draw_map(ax, sim)
    draw_blocked(ax, sim, t)
    for a in sim.agents:
        draw_path(ax, a.path, t, 25, C['agent'], '-', lw=1.0, alpha=0.35)
    draw_agents(ax, sim, t)
    ax.set_title(f'Warehouse at t={t}: {len(sim.agents)} robots with their planned paths (next 25 steps)',
                 fontsize=10, loc='left')
    _legend(ax)
    fig.tight_layout()
    fig.savefig(path, facecolor='white')
    plt.close(fig)




def save_failure_snapshot(sim, path: Path, title: str) -> None:
    """Final state of a run that did not finish: stuck robots in red, with a line to the
    goal each one is stuck on (dashed red X = goal cut off, dashed orange = deadlocked)."""
    t = sim.t
    stuck = sim.stuck_robots()
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=130)
    draw_map(ax, sim)
    draw_blocked(ax, sim, t)
    draw_agents(ax, sim, t, disrupted=set(stuck), size=80)
    for aid, why in (stuck.items() if len(stuck) <= 10 else ()):     # too many lines would hide the map
        a = sim.agents[aid]
        g = a.remaining_goals()[0].cell
        (r0, c0), (r1, c1) = a.pos(t), g
        color = C['disrupted'] if why == 'unreachable' else C['altered']
        ax.plot([c0, c1], [r0, r1], '--', color=color, lw=1.2, alpha=0.8, zorder=5)
        ax.scatter([c1], [r1], s=110, marker='X' if why == 'unreachable' else 'o', facecolor='none' if why != 'unreachable' else color,
                   edgecolor=color, linewidth=1.5, zorder=8)
    n_un = sum(v == 'unreachable' for v in stuck.values())
    m = sim.metrics()
    ax.set_title(f'{title}\nt = {t}: {m["tasks_done"]}/{m["tasks_total"]} tasks done; '
                 f'{len(stuck)} robots stuck ({n_un} goal cut off, {len(stuck) - n_un} deadlocked)',
                 fontsize=9.5, loc='left', color=C['text'])
    _legend(ax, extra=(Line2D([], [], marker='X', ls='--', color=C['disrupted'], label='goal cut off'),
                       Line2D([], [], marker='o', ls='--', mfc='none', color=C['altered'], label='deadlocked: next goal')))
    fig.tight_layout()
    fig.savefig(path, facecolor='white')
    plt.close(fig)
