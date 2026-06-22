"""
hep_heatmap.py — Heatmap of Australian HEP influence scores.

Rows    : HEPs (abbreviation codes from HEP_concordances Keys sheet)
Columns : OA fields (field_idx 11–36) or Leiden groups (field_idx 1–5)
Values  : influence score v from spectral rankings (not ordinal rank)
Ordering: rows by increasing row-sum, columns by increasing column-sum
          → highest-ranked HEPs / most-active fields cluster bottom-right
Colour  : blue (low) → white (mean v=1) → orange (high); gray = NA

Usage:
  .venv/bin/python analysis/hep_heatmap.py                          # 26 OA fields, baseline
  .venv/bin/python analysis/hep_heatmap.py --mode leiden            # 5 Leiden groups, baseline
  .venv/bin/python analysis/hep_heatmap.py --label OECDG20CIA       # OA fields, OECDG20CIA bloc
  .venv/bin/python analysis/hep_heatmap.py --mode leiden --label OECDG20CIA
"""

import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

FIELD_NAMES = {
    11: "Agricultural and Biological Sciences",
    12: "Arts and Humanities",
    13: "Biochemistry, Genetics and Molecular Biology",
    14: "Business, Management and Accounting",
    15: "Chemical Engineering",
    16: "Chemistry",
    17: "Computer Science",
    18: "Decision Sciences",
    19: "Earth and Planetary Sciences",
    20: "Economics, Econometrics and Finance",
    21: "Energy",
    22: "Engineering",
    23: "Environmental Science",
    24: "Immunology and Microbiology",
    25: "Materials Science",
    26: "Mathematics",
    27: "Medicine",
    28: "Neuroscience",
    29: "Nursing",
    30: "Pharmacology, Toxicology and Pharmaceutics",
    31: "Physics and Astronomy",
    32: "Psychology",
    33: "Social Sciences",
    34: "Veterinary",
    35: "Dentistry",
    36: "Health Professions",
}

LEIDEN_NAMES = {
    1: "Maths & CS",
    2: "Physical Sci & Eng",
    3: "Life & Earth Sci",
    4: "Biomed & Health",
    5: "Social Sci & Hum",
}


def build_pivot(working: Path, keys: pd.DataFrame, window: str,
                label: str, mode: str) -> pd.DataFrame:
    keys = keys.copy()
    keys['inst_id'] = keys['institution_idx'].str.lstrip('I').astype('int64')

    if mode == 'leiden':
        fid_range = range(1, 6)
        col_name  = lambda fid: LEIDEN_NAMES[fid]
    else:
        fid_range = range(11, 37)
        col_name  = lambda fid: f"{fid} {FIELD_NAMES[fid].split()[0]}"

    records = []
    for fid in fid_range:
        rk_path = working / f'rankings_{fid}_{window}_{label}.parquet'
        if not rk_path.exists():
            continue
        df = pd.read_parquet(rk_path, columns=['unit_idx', 'unit_type', 'v'])
        df = df[df['unit_type'] == 'U'][['unit_idx', 'v']].copy()
        df['field_idx'] = fid
        records.append(df)

    if not records:
        raise FileNotFoundError(
            f"No ranking files found for mode={mode} label={label} window={window}"
        )

    all_rk = pd.concat(records, ignore_index=True)
    merged = all_rk.merge(keys[['HEP', 'inst_id']], left_on='unit_idx', right_on='inst_id', how='inner')
    pivot  = merged.pivot_table(index='HEP', columns='field_idx', values='v', aggfunc='first')

    for fid in fid_range:
        if fid not in pivot.columns:
            pivot[fid] = np.nan
    pivot = pivot[sorted(pivot.columns)]

    col_sums = pivot.sum(axis=0, min_count=1)
    pivot    = pivot[col_sums.sort_values().index]
    row_sums = pivot.sum(axis=1, min_count=1)
    pivot    = pivot.loc[row_sums.sort_values().index]

    pivot.columns = [col_name(c) for c in pivot.columns]
    return pivot


def plot_heatmap(pivot: pd.DataFrame, title: str, out_path: Path) -> None:
    n_rows, n_cols = pivot.shape

    cmap = LinearSegmentedColormap.from_list(
        'blue_orange', ['#2166ac', '#92c5de', '#f7f7f7', '#fdae61', '#d6604d']
    )
    cmap.set_bad(color='#aaaaaa')

    data   = pivot.values.astype(float)
    CENTER = 1.0
    scaled = np.zeros_like(data)
    for j in range(n_cols):
        col   = data[:, j]
        valid = col[~np.isnan(col)]
        if len(valid) == 0:
            scaled[:, j] = np.nan
            continue
        lo, hi   = valid.min(), valid.max()
        lo_span  = max(CENTER - lo, 1e-9)
        hi_span  = max(hi - CENTER, 1e-9)
        out = np.where(
            col <= CENTER,
            0.5 * (col - lo) / lo_span,
            0.5 + 0.5 * (col - CENTER) / hi_span,
        )
        scaled[:, j] = np.where(np.isnan(col), np.nan, np.clip(out, 0, 1))
    masked_scaled = np.ma.masked_invalid(scaled)

    fig_h = max(6, n_rows * 0.32)
    fig_w = max(6, n_cols * 0.55)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    im = ax.imshow(masked_scaled, aspect='auto', cmap=cmap, vmin=0, vmax=1,
                   interpolation='nearest')

    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(pivot.columns, rotation=45, ha='right', fontsize=8)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(pivot.index, fontsize=8)
    ax.set_ylabel('HEP', labelpad=8)

    ax.set_title(
        title + '\n(white = field mean v=1; rows and columns ordered by increasing sum)',
        fontsize=10,
    )

    cbar = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.02)
    cbar.set_label('Influence relative to field mean (v=1)', fontsize=8)
    cbar.set_ticks([0, 0.5, 1])
    cbar.set_ticklabels(['low', 'mean', 'high'])

    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f"Saved: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    parser.add_argument('--mode',   default='fields', choices=['fields', 'leiden'],
                        help='fields = 26 OA fields; leiden = 5 Leiden groups')
    parser.add_argument('--out',    default=None,
                        help='output path; default: analysis/hep_heatmap_{mode}_{label}.pdf')
    args = parser.parse_args()

    if args.out is None:
        args.out = f'analysis/plots/hep_heatmap_{args.mode}_{args.label}.pdf'

    paths = load_config()
    keys  = pd.read_excel('data/HEP_concordances.xlsx', sheet_name='Keys')

    pivot = build_pivot(paths.working, keys, args.window, args.label, args.mode)
    print(f"Pivot: {pivot.shape}  ({pivot.index.nunique()} HEPs × {len(pivot.columns)} cols)")
    print(pivot.sum(axis=1, min_count=1).sort_values().to_string())

    mode_str  = 'Leiden group' if args.mode == 'leiden' else 'OA field'
    label_str = args.label
    title     = f'Australian HEP influence scores by {mode_str}  [{label_str}]'

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plot_heatmap(pivot, title, out_path)


if __name__ == '__main__':
    main()
