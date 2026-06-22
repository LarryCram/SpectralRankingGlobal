"""
impact_facets.py — Leiden group spectral ranking comparison plots.

Produces two PDFs per comparison:

  leiden_{cmp}_ordinal.pdf    — x=baseline rank, y=log₁₀(v)
                                 baseline as black line, comparison as colour scatter
  leiden_{cmp}_influence.pdf  — x=log₁₀(v_baseline), y=log₁₀(v_comparison)
                                 scatter of jointly-retained units; diagonal y=x = no change

Both PDFs: 2 rows × 5 columns (Sources upper / Institutions lower × Leiden groups 1–5).

Usage:
  .venv/bin/python analysis/impact_facets.py                          # vs OECDG20CIA (default)
  .venv/bin/python analysis/impact_facets.py --cmp tau20             # vs sensitivity variant
  .venv/bin/python analysis/impact_facets.py --cmp all               # all known comparisons
  .venv/bin/python analysis/impact_facets.py --window 2020_2024 --out-dir analysis/plots
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

CMP_STYLE = {
    'OECDG20':    ('OECDG20',     '#e6ab02'),
    'OECDG20CIA': ('OECDG20−CIA', '#a65628'),
    'CIAA':       ('CIAA',        '#e41a1c'),
    'tau20':      ('τ=20',        '#2ca25f'),
    'rho1':       ('ρ=1',         '#2ca25f'),
    'eps1':       ('ε=1',         '#2ca25f'),
    'om1':        ('ω=1',         '#2ca25f'),
    'beta1':      ('β=1',         '#2ca25f'),
}

_LOG_LO, _LOG_HI = -2.35, 1.35
_LOG_TICKS = [-1, 0, 1]


def _lv(v) -> np.ndarray:
    return np.log10(np.clip(np.asarray(v, dtype=float), 1e-10, None))


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


def _panel_title(row: int, col: int, lid: int) -> str:
    """Column header with row unit-type label in the leftmost column."""
    name = LEIDEN_NAMES[lid]
    if row == 0:
        return ('Sources\n' + name) if col == 0 else name
    else:
        return 'Institutions' if col == 0 else ''


# ── ordinal plot (x=baseline rank, y=log v) ──────────────────────────────────

def draw_ordinal_panel(ax, base_df: pd.DataFrame, cmp_df: pd.DataFrame | None,
                       unit_type: str,
                       cmp_color: str, cmp_disp: str,
                       title: str = '', ylabel: str = '', xlabel: bool = True) -> None:

    base = base_df[base_df['unit_type'] == unit_type][['unit_idx', 'rank_v', 'v']].copy()
    base_sorted = base.sort_values('rank_v')
    n_base = len(base)

    ax.plot(
        base_sorted['rank_v'].values,
        _lv(base_sorted['v'].values),
        color='#1a1a1a', linewidth=1.1, alpha=0.9, zorder=5,
        label=f'baseline  (n={n_base:,})',
    )

    if cmp_df is not None:
        cmp = cmp_df[cmp_df['unit_type'] == unit_type][['unit_idx', 'rank_v', 'v']].copy()
        base_rank_map = base.set_index('unit_idx')['rank_v'].to_dict()
        cmp['baseline_rank'] = cmp['unit_idx'].map(base_rank_map)
        cmp = cmp.dropna(subset=['baseline_rank']).sort_values('baseline_rank')
        ax.scatter(
            cmp['baseline_rank'].values,
            _lv(cmp['v'].values),
            color=cmp_color, marker='o', s=2, linewidths=0.6,
            alpha=0.7, zorder=3,
            label=f'{cmp_disp}  (n={len(cmp):,})',
        )

    ax.axhline(0.0, color='#999999', linewidth=0.8, linestyle='--', zorder=0)
    ax.set_ylim(_LOG_LO, _LOG_HI)
    ax.set_yticks(_LOG_TICKS)
    ax.set_xlim(1, n_base)
    if title:
        ax.set_title(title, fontsize=9, pad=5)
    if ylabel:
        ax.set_ylabel(ylabel, labelpad=4)
    ax.set_xlabel('Baseline rank' if xlabel else '', labelpad=3)
    ax.legend(fontsize=7, framealpha=0.85, loc='lower left',
              markerscale=2.0, handlelength=1.2)


# ── influence plot (x=log v_baseline, y=log v_cmp) ───────────────────────────

def draw_influence_panel(ax, base_df: pd.DataFrame, cmp_df: pd.DataFrame | None,
                         unit_type: str,
                         cmp_color: str, cmp_disp: str,
                         title: str = '', ylabel: str = '', xlabel: bool = True) -> None:

    base = base_df[base_df['unit_type'] == unit_type][['unit_idx', 'v']].copy()
    n_base = len(base)

    ax.plot([_LOG_LO, _LOG_HI], [_LOG_LO, _LOG_HI],
            color='#999999', linewidth=0.8, linestyle='--', zorder=0)

    if cmp_df is not None:
        cmp = cmp_df[cmp_df['unit_type'] == unit_type][['unit_idx', 'v']].copy()
        merged = base.merge(cmp, on='unit_idx', suffixes=('_base', '_cmp'))
        ax.scatter(
            _lv(merged['v_base'].values),
            _lv(merged['v_cmp'].values),
            color=cmp_color, marker='o', s=2, linewidths=0.6,
            alpha=0.7, zorder=3,
            label=f'{cmp_disp}  (n={len(merged):,})',
        )

    ax.set_xlim(_LOG_LO, _LOG_HI)
    ax.set_ylim(_LOG_LO, _LOG_HI)
    ax.set_xticks(_LOG_TICKS)
    ax.set_yticks(_LOG_TICKS)
    if title:
        ax.set_title(title, fontsize=9, pad=5)
    if ylabel:
        ax.set_ylabel(ylabel, labelpad=4)
    if xlabel:
        ax.set_xlabel(r'$\log_{10}(v_{\rm baseline})$', labelpad=3)
    ax.text(_LOG_LO + 0.08, 0.70, f'baseline  n={n_base:,}',
            ha='left', va='center', fontsize=8, color='#111111',
            bbox=dict(boxstyle='round,pad=0.15', facecolor='white', alpha=0.8, edgecolor='none'))
    ax.legend(fontsize=7, framealpha=0.85, loc='upper left',
              markerscale=2.0, handlelength=1.2)


# ── figure builders ──────────────────────────────────────────────────────────

def _make_figure(
    working: Path, window: str, cmp_label: str,
    cmp_disp: str, cmp_color: str, out_path: Path,
    draw_fn, figsize: tuple, ylabel_fn, suptitle: str, y: float,
    sharex: bool = False,
) -> None:
    fig, axes = plt.subplots(2, 5, figsize=figsize, sharex=sharex, sharey=True,
                             gridspec_kw={'hspace': 0.38, 'wspace': 0.08})
    for col, lid in enumerate(LEIDEN_NAMES):
        base_df, _ = load_rankings(working, lid, window, 'baseline')
        cmp_df, _  = load_rankings(working, lid, window, cmp_label) if cmp_label != 'baseline' else (None, {})
        if base_df is None:
            continue
        for row, utype in enumerate(('S', 'U')):
            draw_fn(axes[row, col], base_df, cmp_df,
                    unit_type=utype, cmp_color=cmp_color, cmp_disp=cmp_disp,
                    title=_panel_title(row, col, lid),
                    ylabel=ylabel_fn(col), xlabel=(row == 1))
            if not sharex and col > 0:
                axes[row, col].tick_params(labelleft=False)
    fig.suptitle(suptitle, fontsize=10, y=y)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches='tight', dpi=150)
    print(f'Saved: {out_path}')
    plt.close(fig)


def make_ordinal(working: Path, window: str, cmp_label: str,
                 cmp_disp: str, cmp_color: str, out_path: Path) -> None:
    _make_figure(
        working, window, cmp_label, cmp_disp, cmp_color, out_path,
        draw_fn=draw_ordinal_panel,
        figsize=(16, 7), sharex=False,
        ylabel_fn=lambda col: r'$\log_{10}(v)$' if col == 0 else '',
        suptitle=f'Spectral ranking: baseline vs {cmp_disp}  (window {window})',
        y=1.01,
    )


def make_influence(working: Path, window: str, cmp_label: str,
                   cmp_disp: str, cmp_color: str, out_path: Path) -> None:
    _make_figure(
        working, window, cmp_label, cmp_disp, cmp_color, out_path,
        draw_fn=draw_influence_panel,
        figsize=(14, 7), sharex=True,
        ylabel_fn=lambda col: rf'$\log_{{10}}(v_{{\rm {cmp_disp}}})$' if col == 0 else '',
        suptitle=f'Influence comparison: baseline vs {cmp_disp}  (window {window})',
        y=1.02,
    )


# ── main ─────────────────────────────────────────────────────────────────────

ALL_CMPS = ['OECDG20', 'OECDG20CIA', 'CIAA', 'tau20', 'rho1', 'eps1', 'om1', 'beta1']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cmp', nargs='+',
                        default=['OECDG20CIA', 'tau20', 'rho1', 'eps1', 'om1', 'beta1'],
                        help='comparison label(s); use --cmp all for every known label')
    parser.add_argument('--window',  default='2020_2024')
    parser.add_argument('--out-dir', default='analysis/plots')
    args = parser.parse_args()

    cmps = ALL_CMPS if args.cmp == ['all'] else args.cmp

    paths = load_config()
    _pub_theme()
    out_dir = Path(args.out_dir)

    for cmp in cmps:
        cmp_disp, cmp_color = CMP_STYLE.get(cmp, (cmp, '#2ca25f'))
        make_ordinal(
            paths.working, args.window, cmp, cmp_disp, cmp_color,
            out_dir / f'leiden_{cmp}_ordinal.pdf',
        )
        if cmp != 'baseline':
            make_influence(
                paths.working, args.window, cmp, cmp_disp, cmp_color,
                out_dir / f'leiden_{cmp}_influence.pdf',
            )


if __name__ == '__main__':
    main()
