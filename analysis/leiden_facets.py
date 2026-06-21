"""
leiden_facets.py — Log(v) vs rank for all 5 Leiden groups.

Layout: 2 rows × 5 columns
  Row 0  : Sources
  Row 1  : Institutions
  Columns: Leiden groups 1–5

Baseline is drawn as a black line (x-axis = baseline rank).
Sensitivity variants are overlaid as coloured scatter points, x-axis locked
to the same baseline rank so rank displacements are directly visible.

Format mirrors EconomicsBusiness/spectral_results_analysis/fig_5:
  x-axis : rank by v score within the baseline (1 = highest)
  y-axis : log₁₀(v)
  y-range: [-2.35, 1.35], ticks at -1, 0, 1
  Dashed grey line at y=0 (v=1, the mean)

Usage:
  .venv/bin/python analysis/leiden_facets.py
  .venv/bin/python analysis/leiden_facets.py --labels baseline tau20 rho1 eps1 om1 beta1
  .venv/bin/python analysis/leiden_facets.py --window 2020_2024 --out analysis/leiden_facets.pdf
"""

import sys
import json
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

LEIDEN_NAMES = {
    1: 'Mathematics and\nComputer Science',
    2: 'Physical Sciences\nand Engineering',
    3: 'Life and\nEarth Sciences',
    4: 'Biomedical and\nHealth Sciences',
    5: 'Social Sciences\nand Humanities',
}

# Visual style per label — baseline is a line, variants are scatter points
STYLE = {
    'baseline': dict(color='#1a1a1a', marker=None, lw=1.1, alpha_vis=0.9, zorder=7),
    'tau20':    dict(color='#e41a1c', marker='o',  s=2,   lw=0.6, alpha_vis=0.7, zorder=5),
    'rho1':     dict(color='#377eb8', marker='o',  s=2,   lw=0.6, alpha_vis=0.7, zorder=4),
    'eps1':     dict(color='#4daf4a', marker='o',  s=2,   lw=0.6, alpha_vis=0.7, zorder=3),
    'om1':      dict(color='#ff7f00', marker='o',  s=2,   lw=0.6, alpha_vis=0.7, zorder=6),
    'beta1':      dict(color='#984ea3', marker='o',  s=2,   lw=0.6, alpha_vis=0.7, zorder=2),
    'OECDG20CIA': dict(color='#a65628', marker='o',  s=2,   lw=0.6, alpha_vis=0.7, zorder=1),
    'CIAA':       dict(color='#f781bf', marker='o',  s=2,   lw=0.6, alpha_vis=0.7, zorder=0),
}

DISPLAY_LABEL = {
    'baseline':   'baseline',
    'tau20':      'τ=20',
    'rho1':       'ρ=1',
    'eps1':       'ε=1',
    'om1':        'ω=1',
    'beta1':      'β=1',
    'OECDG20CIA': 'OECDG20−CIA',
    'CIAA':       'CIAA',
}

_LOG_LO, _LOG_HI = -2.35, 1.35
_LOG_TICKS = [-1, 0, 1]


def _lv(v: np.ndarray) -> np.ndarray:
    return np.log10(np.clip(v, 1e-10, None))


def _pub_theme():
    sns.set_theme(style='whitegrid', font_scale=1.05)
    plt.rcParams.update({
        'axes.linewidth':    1.2,
        'xtick.major.width': 1.2, 'ytick.major.width': 1.2,
        'xtick.major.size':  5,   'ytick.major.size':  5,
    })


def load_leiden(working: Path, lid: int, window: str, label: str):
    rk_path   = working / f'rankings_{lid}_{window}_{label}.parquet'
    diag_path = working / f'rankings_{lid}_{window}_{label}_diag.json'
    if not rk_path.exists():
        return None, {}
    df   = pd.read_parquet(rk_path)
    diag = json.loads(diag_path.read_text()) if diag_path.exists() else {}
    return df, diag


def draw_panel(ax, series: dict, unit_key: str,
               n_baseline: int, gap: float,
               title: str = '', ylabel: str = '',
               xlabel: bool = True,
               show_legend: bool = False) -> None:
    """
    series: {label: DataFrame with columns [unit_idx, rank_v, v]}
    unit_key: 'S' or 'U'
    Baseline rank is the x-axis; variants are projected onto it.
    """
    baseline_df = series.get('baseline')
    if baseline_df is None:
        return

    base_rank_map = baseline_df.set_index('unit_idx')['rank_v'].to_dict()

    for label, style in STYLE.items():
        if label not in series:
            continue
        df = series[label]
        if df.empty:
            continue

        is_baseline = style['marker'] is None
        disp = DISPLAY_LABEL.get(label, label)

        if is_baseline:
            df_sorted = df.sort_values('rank_v')
            ax.plot(
                df_sorted['rank_v'].values,
                _lv(df_sorted['v'].values),
                color=style['color'], linewidth=style['lw'],
                alpha=style['alpha_vis'], zorder=style['zorder'],
                label=disp,
            )
        else:
            # Project onto baseline rank axis
            df2 = df.copy()
            df2['baseline_rank'] = df2['unit_idx'].map(base_rank_map)
            df2 = df2.dropna(subset=['baseline_rank']).sort_values('baseline_rank')
            n_overlap = len(df2)
            ax.scatter(
                df2['baseline_rank'].values,
                _lv(df2['v'].values),
                color=style['color'], marker=style['marker'],
                s=style['s'], linewidths=style['lw'],
                alpha=style['alpha_vis'], zorder=style['zorder'],
                label=f'{disp}  ({n_overlap:,}/{n_baseline:,})',
            )

    ax.axhline(0.0, color='#999999', linewidth=0.8, linestyle='--', zorder=0)
    ax.set_ylim(_LOG_LO, _LOG_HI)
    ax.set_yticks(_LOG_TICKS)
    ax.set_xlim(1, n_baseline)
    ax.text(0.97, 0.97, f'gap={gap:.3f}',
            transform=ax.transAxes, ha='right', va='top',
            fontsize=8, color='#555555')
    if title:
        ax.set_title(title, fontsize=9, pad=5)
    if ylabel:
        ax.set_ylabel(ylabel, labelpad=4)
    if xlabel:
        ax.set_xlabel('Baseline rank', labelpad=3)
    else:
        ax.set_xlabel('')
    if show_legend:
        ax.legend(fontsize=7, framealpha=0.85, loc='lower left',
                  markerscale=2.0, handlelength=1.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--labels', nargs='+',
                        default=['baseline', 'tau20', 'rho1', 'eps1', 'om1', 'beta1'])
    parser.add_argument('--out', default='analysis/leiden_facets.pdf')
    args = parser.parse_args()

    paths = load_config()
    _pub_theme()

    fig, axes = plt.subplots(2, 5, figsize=(16, 7),
                             sharey=True,
                             gridspec_kw={'hspace': 0.38, 'wspace': 0.08})

    for col, lid in enumerate(range(1, 6)):
        # Load all requested labels for this leiden group
        series_s: dict[str, pd.DataFrame] = {}
        series_u: dict[str, pd.DataFrame] = {}
        gap = float('nan')

        for lbl in args.labels:
            df, diag = load_leiden(paths.working, lid, args.window, lbl)
            if df is None:
                continue
            if lbl == 'baseline':
                gap = diag.get('spectral_gap', float('nan'))
            series_s[lbl] = df[df['unit_type'] == 'S'][['unit_idx', 'rank_v', 'v']].copy()
            series_u[lbl] = df[df['unit_type'] == 'U'][['unit_idx', 'rank_v', 'v']].copy()

        n_s = len(series_s.get('baseline', pd.DataFrame()))
        n_u = len(series_u.get('baseline', pd.DataFrame()))

        draw_panel(
            axes[0, col], series_s, 'S',
            n_baseline=n_s, gap=gap,
            title=LEIDEN_NAMES[lid],
            ylabel=r'$\log_{10}(v)$' if col == 0 else '',
            xlabel=False,
            show_legend=(col == 0),
        )
        draw_panel(
            axes[1, col], series_u, 'U',
            n_baseline=n_u, gap=gap,
            title='',
            ylabel=r'$\log_{10}(v)$' if col == 0 else '',
            xlabel=True,
            show_legend=False,
        )

        for row in (0, 1):
            if col > 0:
                axes[row, col].tick_params(labelleft=False)

    # Row labels
    axes[0, 0].annotate('Sources', xy=(-0.32, 0.5), xycoords='axes fraction',
                        rotation=90, va='center', ha='center',
                        fontsize=10, fontweight='bold')
    axes[1, 0].annotate('Institutions', xy=(-0.32, 0.5), xycoords='axes fraction',
                        rotation=90, va='center', ha='center',
                        fontsize=10, fontweight='bold')

    label_str = ', '.join(args.labels)
    fig.suptitle(
        f'Spectral influence scores by Leiden main field  '
        f'(window {args.window}  |  {label_str})',
        fontsize=10, y=1.01,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches='tight', dpi=150)
    print(f'Saved: {out}')
    plt.close(fig)


if __name__ == '__main__':
    main()
