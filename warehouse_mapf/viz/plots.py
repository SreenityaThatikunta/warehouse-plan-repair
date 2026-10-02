"""Experiment charts + markdown summary tables from results/*.csv.

    python -m warehouse_mapf.viz.plots [--results results] [--out report/figures]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Fixed categorical order (validated palette slots 1-3); colour follows the strategy.
STRAT = {
    'local': ('#2a78d6', 'Local negotiated repair (ours)', 'o'),
    'solo':  ('#eb6834', 'Solo replan only (ablation)', 's'),
    'full':  ('#1baf7a', 'Full replan from scratch (baseline)', '^'),
}
TIER_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4']
TIER_NAMES = {1: 'Tier 1: solo replan', 2: 'Tier 2: negotiate (R)', 3: 'Tier 3: negotiate (2R)',
              4: 'Tier 4: hold & retry', 5: 'global replan'}
INK, INK2, GRID = '#0b0b0b', '#52514e', '#e9e8e4'

plt.rcParams.update({
    'font.size': 10, 'axes.edgecolor': GRID, 'axes.labelcolor': INK2, 'xtick.color': INK2,
    'ytick.color': INK2, 'axes.titlesize': 11, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left',
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'grid.color': GRID,
    'grid.linewidth': 0.8, 'axes.axisbelow': True, 'legend.frameon': False, 'figure.dpi': 150,
    'savefig.bbox': 'tight', 'savefig.facecolor': 'white',
})


def ci95(x: pd.Series) -> float:
    x = x.dropna()
    return 1.96 * x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else 0.0


def agg(df: pd.DataFrame, by: list[str], col: str) -> pd.DataFrame:
    g = df.groupby(by)[col]
    return pd.DataFrame({'mean': g.mean(), 'ci': g.apply(ci95), 'n': g.size()}).reset_index()


def line_chart(df, x, col, title, xlabel, ylabel, path, strategies=('local', 'solo', 'full'),
               xfmt=None, note=None, ylim0=True):
    fig, ax = plt.subplots(figsize=(6.4, 4))
    a = agg(df, ['strategy', x], col)
    for s in strategies:
        d = a[a.strategy == s].sort_values(x)
        if d.empty:
            continue
        color, label, marker = STRAT[s]
        ax.fill_between(d[x], d['mean'] - d['ci'], d['mean'] + d['ci'], color=color, alpha=0.12, lw=0)
        ax.plot(d[x], d['mean'], color=color, lw=2, marker=marker, ms=6, label=label,
                markeredgecolor='white', markeredgewidth=1)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if xfmt:
        ax.xaxis.set_major_formatter(xfmt)
    ax.set_xticks(sorted(df[x].unique()))
    if ylim0:
        ax.set_ylim(bottom=0)
    ax.legend(loc='upper left', fontsize=8.5)
    if note:
        fig.text(0.01, -0.03, note, fontsize=8, color=INK2, ha='left')
    fig.savefig(path)
    plt.close(fig)


def grouped_bars(df, cat, col, title, ylabel, path, order=None, note=None):
    a = agg(df, ['strategy', cat], col)
    cats = order or sorted(a[cat].unique())
    strategies = [s for s in STRAT if s in set(a.strategy)]
    w = 0.8 / len(strategies)
    fig, ax = plt.subplots(figsize=(7.6, 4))
    xs = np.arange(len(cats))
    for i, s in enumerate(strategies):
        d = a[a.strategy == s].set_index(cat).reindex(cats)
        color, label, _ = STRAT[s]
        pos = xs - 0.4 + w * (i + 0.5)
        ax.bar(pos, d['mean'], width=w - 0.04, color=color, label=label, edgecolor='white', linewidth=1)
        ax.errorbar(pos, d['mean'], yerr=d['ci'], fmt='none', ecolor=INK2, elinewidth=1, capsize=2)
        for p, v, e in zip(pos, d['mean'], d['ci']):
            if not np.isnan(v):
                ax.text(p, v + e + 0.3, f'{v:.1f}', ha='center', va='bottom', fontsize=7.5, color=INK)
    ax.set_xticks(xs, [c.replace(' (', '\n(') for c in cats])
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(axis='x', visible=False)
    ax.margins(y=0.12)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=3, fontsize=8.5)
    if note:
        fig.text(0.01, -0.1, note, fontsize=8, color=INK2, ha='left')
    fig.savefig(path)
    plt.close(fig)


def tier_chart(runs, x, xlabel, title, path):
    d = runs[runs.strategy == 'local'].groupby(x)[[f'tier{k}' for k in (1, 2, 3, 4)]].sum()
    share = d.div(d.sum(axis=1), axis=0) * 100
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    bottom = np.zeros(len(share))
    xs = np.arange(len(share))
    for i, k in enumerate((1, 2, 3, 4)):
        vals = share[f'tier{k}'].values
        ax.bar(xs, vals, bottom=bottom, color=TIER_COLORS[i], label=TIER_NAMES[k], edgecolor='white',
               linewidth=1.5, width=0.7)
        bottom += vals
    ax.set_xticks(xs, [f'{v:g}' if not isinstance(v, float) or v >= 1 else f'{v:.0%}' for v in share.index])
    ax.set_ylim(0, 100)
    ax.set_ylabel('% of per-robot repairs')
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.grid(axis='x', visible=False)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18), ncol=2, fontsize=8)
    fig.savefig(path)
    plt.close(fig)


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    for _, r in df.iterrows():
        out.append('| ' + ' | '.join(str(r[c]) for c in cols) + ' |')
    return '\n'.join(out)


def summary_table(runs, dis, x, xname) -> pd.DataFrame:
    rows = []
    for (xv, s), g in runs.groupby([x, 'strategy']):
        d = dis[(dis[x] == xv) & (dis.strategy == s)]
        rows.append({
            '_x': xv,
            xname: f'{xv:.0%}' if x == 'density' else xv,
            'strategy': s,
            'SoC (mean ± 95% CI)': f'{g.soc.mean():.0f} ± {ci95(g.soc):.0f}',
            'makespan': f'{g.makespan.mean():.0f}',
            'SoC overhead': f'{g.soc_overhead.mean():.0f}',
            'disruptions/run': f'{g.n_disruptions.mean():.1f}',
            'altered / disruption': f'{d.altered.mean():.2f}',
            'extra (collateral) / disruption': f'{d.collateral.mean():.2f}',
            'repair ms': f'{d.cpu_ms.mean():.0f}',
            'msgs / disruption': f'{d.messages.mean():.0f}',
            'collisions': int(g.collisions.sum()),
            'tasks done': f'{g.tasks_done.sum()}/{g.tasks_total.sum()}',
        })
    order = {'local': 0, 'solo': 1, 'full': 2}
    df = pd.DataFrame(rows)
    df['_s'] = df.strategy.map(order)
    return df.sort_values(['_x', '_s']).drop(columns=['_x', '_s'])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', type=Path, default=Path('results'))
    ap.add_argument('--out', type=Path, default=Path('report/figures'))
    args = ap.parse_args()
    R, O = args.results, args.out
    O.mkdir(parents=True, exist_ok=True)
    pct = matplotlib.ticker.PercentFormatter(1.0, decimals=0)
    md = ['# Experiment results', '',
          'Generated by `python -m warehouse_mapf.viz.plots`. Means over seeds; ± is the 95% CI.', '']

    if (R / 'agents_runs.csv').exists():
        runs, dis = pd.read_csv(R / 'agents_runs.csv'), pd.read_csv(R / 'agents_disruptions.csv')
        runs['x'] = runs.n_agents
        dis['x'] = dis.n_agents
        line_chart(runs, 'n_agents', 'soc', 'Total time steps vs. number of robots',
                   'Number of robots', 'Sum of task completion times (steps)', O / 'agents_soc.png',
                   note='Dynamic obstacle density 3%, 2 breakdowns, 2 emergencies per run. Bands: 95% CI.')
        line_chart(runs, 'n_agents', 'soc_overhead', 'Extra time steps caused by disruptions',
                   'Number of robots', 'SoC minus undisrupted SoC (steps)', O / 'agents_overhead.png',
                   ylim0=False)
        line_chart(dis, 'n_agents', 'altered', 'Robots whose plans change per disruption',
                   'Number of robots', 'Plans altered per disruption', O / 'agents_altered.png')
        line_chart(dis, 'n_agents', 'cpu_ms', 'Repair computation time per disruption',
                   'Number of robots', 'CPU time per disruption (ms)', O / 'agents_cpu.png')
        tier_chart(runs, 'n_agents', 'Number of robots', 'Which repair tier resolved each robot (local)',
                   O / 'agents_tiers.png')
        md += ['## Sweep 1: number of robots', '', md_table(summary_table(runs, dis, 'n_agents', 'robots')), '']

    if (R / 'density_runs.csv').exists():
        runs, dis = pd.read_csv(R / 'density_runs.csv'), pd.read_csv(R / 'density_disruptions.csv')
        line_chart(runs, 'density', 'soc', 'Total time steps vs. dynamic obstacle density',
                   'Dynamic obstacle density (share of free cells blocked during the run)',
                   'Sum of task completion times (steps)', O / 'density_soc.png', xfmt=pct, ylim0=False,
                   note='20 robots, 2 breakdowns, 2 emergencies per run. Bands: 95% CI.')
        line_chart(dis, 'density', 'altered', 'Robots whose plans change per disruption',
                   'Dynamic obstacle density', 'Plans altered per disruption', O / 'density_altered.png',
                   xfmt=pct)
        line_chart(runs, 'density', 'soc_overhead', 'Extra time steps caused by disruptions',
                   'Dynamic obstacle density', 'SoC minus undisrupted SoC (steps)', O / 'density_overhead.png',
                   xfmt=pct, ylim0=False)
        tier_chart(runs, 'density', 'Dynamic obstacle density', 'Which repair tier resolved each robot (local)',
                   O / 'density_tiers.png')
        md += ['## Sweep 2: dynamic obstacle density (20 robots)', '',
               md_table(summary_table(runs, dis, 'density', 'density')), '']

    if (R / 'single_disruptions.csv').exists():
        dis = pd.read_csv(R / 'single_disruptions.csv')
        order = ['blockage (temp)', 'blockage (perm)', 'breakdown (temp)', 'breakdown (perm)', 'emergency']
        grouped_bars(dis, 'dtype', 'altered', 'Plans altered to handle a single disruption',
                     'Robots whose plans change', O / 'single_altered.png', order=order,
                     note='One disruption per run; 10–50 robots pooled. Error bars: 95% CI.')
        grouped_bars(dis, 'dtype', 'collateral', 'Extra robots changed beyond those directly hit',
                     'Collateral plan changes', O / 'single_collateral.png', order=order)
        line_chart(dis, 'n_agents', 'altered', 'Single disruption: plans altered vs. robots',
                   'Number of robots', 'Plans altered', O / 'single_altered_vs_agents.png')
        t = dis.groupby(['dtype', 'strategy']).agg(direct=('direct', 'mean'), altered=('altered', 'mean'),
                                                  collateral=('collateral', 'mean'), n=('altered', 'size'))
        t = t.round(2).reset_index()
        t['order'] = t.dtype.map({k: i for i, k in enumerate(order)})
        t['s'] = t.strategy.map({'local': 0, 'solo': 1, 'full': 2})
        t = t.sort_values(['order', 's']).drop(columns=['order', 's'])
        md += ['## Sweep 3: single disruption (10–50 robots pooled)', '',
               '`direct`: robots whose plan the disruption itself invalidated. `altered`: all robots whose '
               'plan changed. `collateral` = altered − direct.', '', md_table(t), '']
        by_n = dis.pivot_table(index='n_agents', columns='strategy', values='altered', aggfunc='mean').round(2)
        md += ['Plans altered per single disruption, by number of robots:', '',
               md_table(by_n.reset_index()), '']

    (R / 'summary.md').write_text('\n'.join(md))
    print(f'figures -> {O}/, tables -> {R / "summary.md"}')


if __name__ == '__main__':
    main()
