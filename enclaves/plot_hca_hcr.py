"""
plot_hca_hcr.py — Scatter: HCW source/institution v vs mean citer v,
with matched-HCR-author overlay.

Left panel:  x = log10(source_v of HCW),    y = log10(mean_citer_v)
Right panel: x = log10(mean_inst_v of HCW), y = log10(mean_citer_v)

mean_citer_v = mean MAX-source-v of retained works that cite the HCW (all fields).
Reference lines at log(v) = 0 (v = 1).  Count of (field, work) pairs shown per quadrant.

Produces one PDF per CWTS Leiden group (5 files), each coloured by OA field within
the group.  Also produces one combined PDF coloured by Leiden group.

Overlay: matched-HCR-authored HCW (hollow black circles) plus <v> (pool mean,
filled circle) and <<v>> (per-person mean, diamond) markers, from
hcr_hca_map.parquet / hca_clusters.parquet / hcw_authorships.parquet.

Input:  WORKING/enclave_hcw_{window}_{label}.parquet
Output: enclaves/plots/enclave_citer_v_{window}_{label}_{tag}.pdf

Usage:
  .venv/bin/python enclaves/plot_hca_hcr.py
  .venv/bin/python enclaves/plot_hca_hcr.py --window 2020_2024 --label baseline
"""

import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib import colormaps

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, FIELD_NAMES

# OA field_idx (11–36) → CWTS Leiden group (1–5)
_LEIDEN_GROUP = {
    11: 3, 12: 5, 13: 3, 14: 5, 15: 2, 16: 2, 17: 1, 18: 5,
    19: 3, 20: 5, 21: 2, 22: 2, 23: 3, 24: 3, 25: 2, 26: 1,
    27: 4, 28: 4, 29: 4, 30: 4, 31: 2, 32: 5, 33: 5, 34: 4,
    35: 4, 36: 4,
}
_LEIDEN_LABEL = {
    1: 'Mathematics & Computer Science',
    2: 'Physical Sciences & Engineering',
    3: 'Life & Earth Sciences',
    4: 'Biomedical & Health Sciences',
    5: 'Social Sciences & Humanities',
}
_LEIDEN_SLUG = {
    1: 'L1_MathCS',
    2: 'L2_PhysEng',
    3: 'L3_LifeEarth',
    4: 'L4_BiomedHealth',
    5: 'L5_SocialHum',
}
_LEIDEN_COLOUR = {
    1: '#377eb8',
    2: '#e41a1c',
    3: '#4daf4a',
    4: '#984ea3',
    5: '#ff7f00',
}

_FIELD_SHORT = {
    11: 'Ag & Bio Sci',
    12: 'Arts & Hum',
    13: 'Biochem & Mol Bio',
    14: 'Business & Mgmt',
    15: 'Chemical Eng',
    16: 'Chemistry',
    17: 'Computer Science',
    18: 'Decision Sci',
    19: 'Earth & Planetary',
    20: 'Economics',
    21: 'Energy',
    22: 'Engineering',
    23: 'Environmental Sci',
    24: 'Immunology & Micro',
    25: 'Materials Sci',
    26: 'Mathematics',
    27: 'Medicine',
    28: 'Neuroscience',
    29: 'Nursing',
    30: 'Pharmacology',
    31: 'Physics & Astron',
    32: 'Psychology',
    33: 'Social Sciences',
    34: 'Veterinary',
    35: 'Dentistry',
    36: 'Health Professions',
}


def load_hcr_works(working: Path, window: str, label: str) -> pd.DataFrame | None:
    """One row per (author_idx, work_idx, field_idx) for matched-HCR-authored HCW.

    Columns: author_idx, work_idx, field_idx, leiden_group,
             source_v, mean_inst_v, mean_citer_v.
    Returns None if any upstream file is missing.
    """
    paths = {
        'map':          working / 'hcr_hca_map.parquet',
        'clusters':     working / 'hca_clusters.parquet',
        'authorships':  working / 'hcw_authorships.parquet',
        'hcw':          working / f'enclave_hcw_{window}_{label}.parquet',
    }
    missing = [name for name, p in paths.items() if not p.exists()]
    if missing:
        print(f'  HCR overlay skipped — missing: {", ".join(missing)}')
        return None

    m = pd.read_parquet(paths['map'], columns=['hca_cluster_hash', 'match_status'])
    m = m[m['match_status'].isin(['unique', 'inst_resolved'])]
    hashes = set(m['hca_cluster_hash'])

    clusters = pd.read_parquet(paths['clusters'], columns=['author_idx', 'cluster_hash'])
    author_idxs = set(clusters.loc[clusters['cluster_hash'].isin(hashes), 'author_idx'])

    auth = pd.read_parquet(paths['authorships'], columns=['work_idx', 'author_idx'])
    auth = auth[auth['author_idx'].isin(author_idxs)]

    hcw = pd.read_parquet(paths['hcw'], columns=[
        'work_idx', 'field_idx', 'source_v', 'mean_inst_v', 'mean_citer_v',
    ])
    hcw = hcw[hcw['work_idx'].isin(set(auth['work_idx']))]

    out = hcw.merge(auth, on='work_idx', how='inner')
    out['leiden_group'] = out['field_idx'].map(_LEIDEN_GROUP)
    return out


def _draw_hcr_overlay(ax: plt.Axes, hcr_sub: pd.DataFrame, x_col: str) -> None:
    """Overlay matched-HCR-authored HCW as hollow black circles, plus
    <v> (pool mean, filled circle) and <<v>> (person-weighted mean, diamond)."""
    sub = hcr_sub.dropna(subset=[x_col, 'mean_citer_v'])
    sub = sub[(sub[x_col] > 0) & (sub['mean_citer_v'] > 0)]
    if sub.empty:
        return

    lx = np.log10(sub[x_col].values)
    ly = np.log10(sub['mean_citer_v'].values)
    ax.scatter(lx, ly, s=12, facecolors='none', edgecolors='k',
               linewidths=0.6, zorder=6)

    pool_x, pool_y = np.log10(sub[x_col]).mean(), np.log10(sub['mean_citer_v']).mean()
    ax.scatter([pool_x], [pool_y], s=80, marker='o', color='k', zorder=7)
    ax.annotate(r'$\langle v \rangle$', (pool_x, pool_y), xytext=(4, 4),
               textcoords='offset points', fontsize=8, fontweight='bold', zorder=7)

    per_person = sub.assign(lx=lx, ly=ly).groupby('author_idx')[['lx', 'ly']].mean()
    person_x, person_y = per_person['lx'].mean(), per_person['ly'].mean()
    ax.scatter([person_x], [person_y], s=80, marker='D', color='k', zorder=7)
    ax.annotate(r'$\langle\langle v \rangle\rangle$', (person_x, person_y), xytext=(4, -10),
               textcoords='offset points', fontsize=8, fontweight='bold', zorder=7)


def _axis_style(ax: plt.Axes) -> None:
    ax.axhline(0, color='k', lw=0.8, alpha=0.4)
    ax.axvline(0, color='k', lw=0.8, alpha=0.4)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f'{int(v):d}'))
    ax.xaxis.set_minor_locator(mticker.AutoMinorLocator())
    ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
    ax.grid(which='minor', color='lightgray', linewidth=0.4)
    ax.tick_params(which='minor', length=0)
    ax.set_xlabel(r'$\log_{10}(v)$ HCW', fontsize=9)


def _annotate_quadrants(ax: plt.Axes, lx: np.ndarray, ly: np.ndarray) -> None:
    """Write count of points in each quadrant near its outer corner."""
    xl, xr = ax.get_xlim()
    yb, yt = ax.get_ylim()
    px = (xr - xl) * 0.02
    py = (yt - yb) * 0.02
    quadrants = [
        ((lx <  0) & (ly <  0), xl + px, yb + py, 'left',  'bottom'),
        ((lx <  0) & (ly >= 0), xl + px, yt - py, 'left',  'top'   ),
        ((lx >= 0) & (ly <  0), xr - px, yb + py, 'right', 'bottom'),
        ((lx >= 0) & (ly >= 0), xr - px, yt - py, 'right', 'top'   ),
    ]
    for mask, x, y, ha, va in quadrants:
        ax.text(x, y, f'{mask.sum():,}', ha=ha, va=va,
                fontsize=8, color='#333333', fontweight='bold')


def _draw_panels(sub: pd.DataFrame, axes, colour_col: str,
                 colour_map: dict, label_map: dict) -> None:
    """Scatter both panels; annotate quadrant counts; colour by colour_col."""
    ly = np.log10(sub['mean_citer_v'].values)
    keys = sorted(colour_map)

    for ax, (x_col, panel_title) in zip(axes, [
        ('source_v',    'Sources'),
        ('mean_inst_v', 'Institutions'),
    ]):
        lx = np.log10(sub[x_col].values)
        for k in keys:
            mask = sub[colour_col].values == k
            ax.scatter(lx[mask], ly[mask],
                       color=colour_map[k], s=5, alpha=0.5,
                       label=label_map[k], linewidths=0)
        _axis_style(ax)
        _annotate_quadrants(ax, lx, ly)
        ax.set_title(panel_title, fontsize=11, pad=6)

    axes[0].set_ylabel(r'$\log_{10}(\bar{v})$ citing sources', fontsize=9)
    axes[1].tick_params(labelleft=False)


def _save_fig(fig: plt.Figure, axes, out_path: Path, n: int,
              legend_title: str = '') -> None:
    handles, labels = axes[0].get_legend_handles_labels()
    leg = axes[1].legend(handles, labels, title=legend_title, fontsize=7,
                         title_fontsize=8, markerscale=4, loc='lower right',
                         bbox_to_anchor=(1.0, 0.1),
                         framealpha=0.85, handletextpad=0.4, borderpad=0.5)
    for lh in leg.legend_handles:
        lh.set_alpha(1.0)
    fig.subplots_adjust(left=0.09, right=0.97, top=0.88, bottom=0.12, wspace=0.12)
    fig.savefig(out_path, bbox_inches='tight')
    plt.close(fig)
    print(f'  Saved {out_path.name}  (n={n:,})')


def plot_combined(df: pd.DataFrame, out_path: Path,
                  hcr_works: pd.DataFrame | None = None) -> None:
    """Single plot, all fields, coloured by Leiden group."""
    sub = df.dropna(subset=['source_v', 'mean_inst_v', 'mean_citer_v']).copy()
    sub = sub[(sub['source_v'] > 0) & (sub['mean_inst_v'] > 0) & (sub['mean_citer_v'] > 0)]
    sub['leiden_group'] = sub['field_idx'].map(_LEIDEN_GROUP)

    fig, axes = plt.subplots(1, 2, figsize=(10, 5), sharey=True)
    _draw_panels(sub, axes, 'leiden_group', _LEIDEN_COLOUR, _LEIDEN_LABEL)
    if hcr_works is not None:
        for ax, x_col in zip(axes, ['source_v', 'mean_inst_v']):
            _draw_hcr_overlay(ax, hcr_works, x_col)
    fig.suptitle('HCW: source/institution v vs mean citing source v — all fields',
                 fontsize=10)
    _save_fig(fig, axes, out_path, len(sub), legend_title='Leiden group')


def plot_leiden_group(df: pd.DataFrame, g: int, out_path: Path,
                      hcr_works: pd.DataFrame | None = None) -> None:
    """One plot for a single Leiden group, coloured by OA field."""
    fields_in_group = sorted(k for k, v in _LEIDEN_GROUP.items() if v == g)
    sub = df[df['field_idx'].isin(fields_in_group)].copy()
    sub = sub.dropna(subset=['source_v', 'mean_inst_v', 'mean_citer_v'])
    sub = sub[(sub['source_v'] > 0) & (sub['mean_inst_v'] > 0) & (sub['mean_citer_v'] > 0)]

    cmap = colormaps['tab10']
    colour_map = {fid: cmap(i / max(len(fields_in_group) - 1, 1))
                  for i, fid in enumerate(fields_in_group)}
    label_map  = {fid: _FIELD_SHORT.get(fid, str(fid)) for fid in fields_in_group}

    fig, axes = plt.subplots(1, 2, figsize=(10, 5), sharey=True)
    _draw_panels(sub, axes, 'field_idx', colour_map, label_map)
    if hcr_works is not None:
        hcr_sub = hcr_works[hcr_works['leiden_group'] == g]
        for ax, x_col in zip(axes, ['source_v', 'mean_inst_v']):
            _draw_hcr_overlay(ax, hcr_sub, x_col)
    fig.suptitle(f'HCW: {_LEIDEN_LABEL[g]}', fontsize=10)
    _save_fig(fig, axes, out_path, len(sub), legend_title='OA field')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    args = parser.parse_args()

    paths    = load_config()
    hcw_path = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    if not hcw_path.exists():
        print(f'ERROR: {hcw_path} not found — run build_enclave_hcw.py first')
        sys.exit(1)

    df = pd.read_parquet(hcw_path)
    n_works  = df['work_idx'].nunique()
    n_fields = df['field_idx'].nunique()
    print(f'Loaded {len(df):,} HCW rows  |  {n_fields} fields  |  {n_works:,} distinct works')

    out_dir = Path(__file__).parent / 'plots'
    out_dir.mkdir(exist_ok=True)
    tag = f'{args.window}_{args.label}'

    hcr_works = load_hcr_works(paths.working, args.window, args.label)
    if hcr_works is not None:
        print(f'  {len(hcr_works):,} HCR-authored HCW rows  |  '
              f'{hcr_works["author_idx"].nunique()} distinct matched-HCR authors')

    print('Plotting...')
    plot_combined(df, out_dir / f'enclave_citer_v_{tag}_all.pdf', hcr_works=hcr_works)
    for g in range(1, 6):
        plot_leiden_group(df, g, out_dir / f'enclave_citer_v_{tag}_{_LEIDEN_SLUG[g]}.pdf',
                          hcr_works=hcr_works)


if __name__ == '__main__':
    main()
