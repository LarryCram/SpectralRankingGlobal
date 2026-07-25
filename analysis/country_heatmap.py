"""
country_heatmap.py — Heatmap of top-N institution influence scores for a country.

Rows    : top N institutions in the country by distinct work count in the window
Columns : Leiden groups (1–5) or OA fields (11–36)
Values  : influence score v from spectral rankings
Ordering: rows by increasing row-sum, columns by increasing column-sum
Colour  : blue (low) → white (mean v=1) → orange (high); gray = NA

Also prints a mnemonic → full name mapping table.

Usage:
  .venv/bin/python analysis/country_heatmap.py                           # AU, leiden, top 30
  .venv/bin/python analysis/country_heatmap.py --country IN
  .venv/bin/python analysis/country_heatmap.py --country AU --mode fields
  .venv/bin/python analysis/country_heatmap.py --country AU --n 50 --label OECDG20CIA
"""

import re
import sys
import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import duckdb
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings
from util.runs import FIELD_NAMES

_LEIDEN_COL_NAMES = {
    1: 'Maths&CS',
    2: 'Phys&Eng',
    3: 'Life&Earth',
    4: 'Biomed',
    5: 'Soc&Hum',
}

_COL_NAMES = {
    'leiden': _LEIDEN_COL_NAMES,
    'fields': FIELD_NAMES,
}

_FID_RANGE = {
    'leiden': range(1, 6),
    'fields': range(11, 37),
}

_STOP    = frozenset({'of', 'the', 'and', 'for', 'in', 'at', 'a', 'de', 'la', 'du', 'des'})
_GENERIC = frozenset({'university', 'institute', 'college', 'school', 'hospital',
                      'center', 'centre', 'academy', 'polytechnic'})


# ── mnemonic generation ───────────────────────────────────────────────────────

def _raw_mnemonic(name: str) -> str:
    words = [w for w in re.split(r'[\s\-,./()]+', name) if w and w.lower() not in _STOP]
    if not words:
        return name[:4].upper()

    initials = ''.join(w[0].upper() for w in words)

    if len(initials) >= 3:
        return initials[:4]

    if len(initials) == 1:
        return words[0][:4].upper()

    # 2 initials — extend using the most distinctive word
    non_gen = [w for w in words if w.lower() not in _GENERIC]
    gen     = [w for w in words if w.lower() in _GENERIC]

    if non_gen and gen:
        ng, gi = non_gen[0], gen[0][0].upper()
        if words.index(non_gen[0]) < words.index(gen[0]):
            return (ng[:3] + gi)[:4]        # e.g. MonU (Monash University)
        else:
            return (gi + ng[:3])[:4]        # e.g. UMel (University of Melbourne)
    elif len(non_gen) >= 2:
        return (non_gen[0][:3] + non_gen[1][0].upper())[:4]
    elif non_gen:
        return non_gen[0][:4].upper()
    return words[0][:4].upper()


def make_mnemonics(names: list[str]) -> dict[str, str]:
    """Return name → unique 3-4 char mnemonic. Collisions get a numeric suffix."""
    raw    = {n: _raw_mnemonic(n) for n in names}
    counts = Counter(raw.values())
    seen: dict[str, int] = {}
    result: dict[str, str] = {}
    for name, mnem in raw.items():
        if counts[mnem] > 1:
            seen[mnem] = seen.get(mnem, 0) + 1
            result[name] = f"{mnem}{seen[mnem]}"
        else:
            result[name] = mnem
    return result


# ── data loading ──────────────────────────────────────────────────────────────

def get_top_institutions(fw_path: str, tc0: int, tc1: int,
                         country: str, n: int) -> pd.DataFrame:
    """Return top-N (institution_idx, n_works) for country in window."""
    with duckdb.connect() as db:
        return db.execute(f"""
            SELECT institution_idx,
                   COUNT(DISTINCT work_idx) AS n_works
            FROM '{fw_path}'
            WHERE country_code = '{country}'
              AND publication_year BETWEEN {tc0} AND {tc1}
              AND institution_idx IS NOT NULL
            GROUP BY institution_idx
            ORDER BY n_works DESC
            LIMIT {n}
        """).df()


def get_institution_names(inst_indices: list[int],
                          institutions_path: str) -> dict[int, str]:
    ids_sql = ', '.join(str(i) for i in inst_indices)
    with duckdb.connect() as db:
        df = db.execute(f"""
            SELECT CAST(REGEXP_REPLACE(id, 'https://openalex.org/I', '') AS BIGINT) AS idx,
                   display_name
            FROM '{institutions_path}'
            WHERE CAST(REGEXP_REPLACE(id, 'https://openalex.org/I', '') AS BIGINT)
                  IN ({ids_sql})
        """).df()
    return dict(zip(df['idx'], df['display_name']))


def build_pivot(working: Path, inst_idx_list: list[int],
                window: str, label: str, mode: str) -> pd.DataFrame:
    """v-score pivot: index=institution_idx (int), columns=field_idx (int)."""
    fid_range = _FID_RANGE[mode]
    records   = []
    for fid in fid_range:
        rk_path = working / f'rankings_{fid}_{window}_{label}.parquet'
        if not rk_path.exists():
            continue
        df = pd.read_parquet(rk_path, columns=['unit_idx', 'unit_type', 'v'])
        df = df[(df['unit_type'] == 'U') & (df['unit_idx'].isin(inst_idx_list))][['unit_idx', 'v']].copy()
        df['fid'] = fid
        records.append(df)
    if not records:
        raise FileNotFoundError(
            f"No ranking files found for mode={mode} window={window} label={label}"
        )
    all_rk = pd.concat(records, ignore_index=True)
    pivot  = all_rk.pivot_table(index='unit_idx', columns='fid',
                                 values='v', aggfunc='first')
    for fid in fid_range:
        if fid not in pivot.columns:
            pivot[fid] = np.nan
    return pivot[sorted(pivot.columns)]


# ── plotting ──────────────────────────────────────────────────────────────────

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
        lo, hi  = valid.min(), valid.max()
        lo_span = max(CENTER - lo, 1e-9)
        hi_span = max(hi - CENTER, 1e-9)
        out = np.where(
            col <= CENTER,
            0.5 * (col - lo) / lo_span,
            0.5 + 0.5 * (col - CENTER) / hi_span,
        )
        scaled[:, j] = np.where(np.isnan(col), np.nan, np.clip(out, 0, 1))
    masked = np.ma.masked_invalid(scaled)

    fig_h = max(6, n_rows * 0.32)
    fig_w = max(5, n_cols * 0.9)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    im = ax.imshow(masked, aspect='auto', cmap=cmap, vmin=0, vmax=1,
                   interpolation='nearest')
    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(pivot.columns.tolist(), rotation=45, ha='right', fontsize=9)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(pivot.index.tolist(), fontsize=8)
    ax.set_title(title + '\n(white = field mean v=1; ordered by row/col sum)',
                 fontsize=10)

    cbar = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.02)
    cbar.set_label('Influence relative to field mean (v=1)', fontsize=8)
    cbar.set_ticks([0, 0.5, 1])
    cbar.set_ticklabels(['low', 'mean', 'high'])

    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved: {out_path}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--country', default='AU')
    parser.add_argument('--n',       type=int, default=40)
    parser.add_argument('--window',  default='2020_2024')
    parser.add_argument('--label',   default='baseline')
    parser.add_argument('--mode',    default='leiden', choices=['leiden', 'fields'])
    parser.add_argument('--out',     default=None)
    args = parser.parse_args()

    if args.out is None:
        args.out = f'analysis/plots/country_heatmap_{args.country}_{args.mode}_{args.label}.pdf'

    paths    = load_config()
    settings = load_settings()
    fw_path  = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    inst_p   = f'{paths.openalex}/institutions.parquet'
    tc0, tc1 = (int(x) for x in args.window.split('_'))

    print(f"Top {args.n} institutions in {args.country}  window={args.window}  label={args.label}")
    top_inst = get_top_institutions(fw_path, tc0, tc1, args.country, args.n)
    if top_inst.empty:
        raise ValueError(f"No institutions found for country={args.country!r}")
    inst_idx_list = top_inst['institution_idx'].tolist()

    names      = get_institution_names(inst_idx_list, inst_p)
    name_list  = [names.get(i, f'I{i}') for i in inst_idx_list]
    mnemonics  = make_mnemonics(name_list)
    idx_to_mnem = {i: mnemonics[names.get(i, f'I{i}')] for i in inst_idx_list}

    print("\nBuilding v-score pivot...")
    pivot = build_pivot(paths.working, inst_idx_list, args.window, args.label, args.mode)

    col_sums = pivot.sum(axis=0, min_count=1)
    pivot    = pivot[col_sums.sort_values().index]
    row_sums = pivot.sum(axis=1, min_count=1)
    pivot    = pivot.loc[row_sums.sort_values().index]

    col_names = _COL_NAMES[args.mode]
    pivot.index   = [idx_to_mnem.get(i, str(i)) for i in pivot.index]
    pivot.columns = [col_names[c] for c in pivot.columns]

    print(f"\n{'Mnem':<8}  {'n_works':>8}  Institution")
    print('─' * 72)
    for idx, n_works in zip(top_inst['institution_idx'], top_inst['n_works']):
        print(f"{idx_to_mnem.get(idx, '?'):<8}  {n_works:>8,}  {names.get(idx, f'I{idx}')}")

    n_ranked = len(pivot)
    n_total  = len(inst_idx_list)
    if n_ranked < n_total:
        print(f"\nNote: {n_total - n_ranked} institution(s) absent from all Leiden rankings "
              f"(below τ threshold in every field) — shown as gray rows if included.")

    mode_str = 'Leiden group' if args.mode == 'leiden' else 'OA field'
    title    = f"{args.country} top-{args.n} institutions by {mode_str}  [{args.label}]"
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plot_heatmap(pivot, title, out_path)


if __name__ == '__main__':
    main()
