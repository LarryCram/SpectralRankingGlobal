"""
leiden_bloc_facets.py — Baseline vs one comparison label, by Leiden group.

Produces one PDF per comparison (7 total):
  leiden_OECDG20CIA.pdf   — baseline vs OECDG20−CIA bloc
  leiden_CIAA.pdf         — baseline vs CIAA bloc
  leiden_tau20.pdf        — baseline vs τ=20 variant
  leiden_rho1.pdf         — baseline vs ρ=1  variant
  leiden_eps1.pdf         — baseline vs ε=1  variant
  leiden_om1.pdf          — baseline vs ω=1  variant
  leiden_beta1.pdf        — baseline vs β=1  variant

Each PDF: 2 rows × 5 columns (Sources / Institutions × Leiden 1–5).
  Black line  : baseline (global, all countries, standard parameters)
  Colour dots : comparison run, projected onto baseline rank axis

Usage:
  .venv/bin/python analysis/leiden_bloc_facets.py [--window 2020_2024] [--out-dir analysis]
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

_VARIANT_GREEN = '#2ca25f'

# (file_label, display_label, scatter_color)
COMPARISONS = [
    ('OECDG20CIA', 'OECDG20−CIA', '#a65628'),
    ('CIAA',       'CIAA',        '#e41a1c'),
    ('tau20',      'τ=20',        _VARIANT_GREEN),
    ('rho1',       'ρ=1',         _VARIANT_GREEN),
    ('eps1',       'ε=1',         _VARIANT_GREEN),
    ('om1',        'ω=1',         _VARIANT_GREEN),
    ('beta1',      'β=1',         _VARIANT_GREEN),
]

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


def load_rankings(working: Path, lid: int, window: str, label: str):
    rk_path   = working / f'rankings_{lid}_{window}_{label}.parquet'
    diag_path = working / f'rankings_{lid}_{window}_{label}_diag.json'
    if not rk_path.exists():
        return None, {}
    df   = pd.read_parquet(rk_path)
    diag = json.loads(diag_path.read_text()) if diag_path.exists() else {}
    return df, diag


def draw_panel(ax, base_df: pd.DataFrame, cmp_df: pd.DataFrame | None,
               unit_type: str, gap: float, cmp_color: str, cmp_disp: str,
               title: str = '', ylabel: str = '', xlabel: bool = True,
               show_legend: bool = False) -> None:

    base = base_df[base_df['unit_type'] == unit_type][['unit_idx', 'rank_v', 'v']].copy()
    base_sorted = base.sort_values('rank_v')
    n_base = len(base)

    ax.plot(
        base_sorted['rank_v'].values,
        _lv(base_sorted['v'].values),
        color='#1a1a1a', linewidth=1.1, alpha=0.9, zorder=5, label='baseline',
    )

    if cmp_df is not None:
        cmp = cmp_df[cmp_df['unit_type'] == unit_type][['unit_idx', 'rank_v', 'v']].copy()
        base_rank_map = base.set_index('unit_idx')['rank_v'].to_dict()
        cmp['baseline_rank'] = cmp['unit_idx'].map(base_rank_map)
        cmp = cmp.dropna(subset=['baseline_rank']).sort_values('baseline_rank')
        n_overlap = len(cmp)
        ax.scatter(
            cmp['baseline_rank'].values,
            _lv(cmp['v'].values),
            color=cmp_color, marker='o', s=2, linewidths=0.6,
            alpha=0.7, zorder=3,
            label=f'{cmp_disp}  ({n_overlap:,}/{n_base:,})',
        )

    ax.axhline(0.0, color='#999999', linewidth=0.8, linestyle='--', zorder=0)
    ax.set_ylim(_LOG_LO, _LOG_HI)
    ax.set_yticks(_LOG_TICKS)
    ax.set_xlim(1, n_base)
    ax.text(0.97, 0.97, f'gap={gap:.3f}',
            transform=ax.transAxes, ha='right', va='top',
            fontsize=8, color='#555555')
    if title:
        ax.set_title(title, fontsize=9, pad=5)
    if ylabel:
        ax.set_ylabel(ylabel, labelpad=4)
    ax.set_xlabel('Baseline rank' if xlabel else '', labelpad=3)
    if show_legend:
        ax.legend(fontsize=8, framealpha=0.85, loc='lower left',
                  markerscale=2.0, handlelength=1.2)


def make_pdf(working: Path, window: str, file_label: str,
             cmp_disp: str, cmp_color: str, out_path: Path) -> None:
    _pub_theme()
    fig, axes = plt.subplots(2, 5, figsize=(16, 7),
                             sharey=True,
                             gridspec_kw={'hspace': 0.38, 'wspace': 0.08})

    for col, lid in enumerate(range(1, 6)):
        base_df, diag = load_rankings(working, lid, window, 'baseline')
        cmp_df, _     = load_rankings(working, lid, window, file_label)
        gap = diag.get('spectral_gap', float('nan')) if diag else float('nan')

        if base_df is None:
            continue

        draw_panel(
            axes[0, col], base_df, cmp_df,
            unit_type='S', gap=gap,
            cmp_color=cmp_color, cmp_disp=cmp_disp,
            title=LEIDEN_NAMES[lid],
            ylabel=r'$\log_{10}(v)$' if col == 0 else '',
            xlabel=False,
            show_legend=(col == 0),
        )
        draw_panel(
            axes[1, col], base_df, cmp_df,
            unit_type='U', gap=gap,
            cmp_color=cmp_color, cmp_disp=cmp_disp,
            title='',
            ylabel=r'$\log_{10}(v)$' if col == 0 else '',
            xlabel=True,
            show_legend=False,
        )

        for row in (0, 1):
            if col > 0:
                axes[row, col].tick_params(labelleft=False)

    axes[0, 0].annotate('Sources', xy=(-0.32, 0.5), xycoords='axes fraction',
                        rotation=90, va='center', ha='center',
                        fontsize=10, fontweight='bold')
    axes[1, 0].annotate('Institutions', xy=(-0.32, 0.5), xycoords='axes fraction',
                        rotation=90, va='center', ha='center',
                        fontsize=10, fontweight='bold')

    fig.suptitle(
        f'Spectral influence: baseline vs {cmp_disp}  (window {window})',
        fontsize=10, y=1.01,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches='tight', dpi=150)
    print(f'Saved: {out_path}')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window',  default='2020_2024')
    parser.add_argument('--out-dir', default='analysis')
    args = parser.parse_args()

    paths   = load_config()
    out_dir = Path(args.out_dir)

    for file_label, cmp_disp, cmp_color in COMPARISONS:
        out_path = out_dir / f'leiden_{file_label}.pdf'
        make_pdf(paths.working, args.window, file_label, cmp_disp, cmp_color, out_path)


if __name__ == '__main__':
    main()
