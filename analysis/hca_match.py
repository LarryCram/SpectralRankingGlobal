"""
hca_match.py — OAX HCA name clustering (parallel to hcr_match.py).

Phase 1:  Load hca_authors.parquet; apply per-field n_hcw threshold; deduplicate
          to one row per author_idx; parse display_name via HumanName; build name
          index; assign partition-aware cluster_hash per name cluster.
Phase 1b: Report name-parse flags (titles, suffixes) and unresolved name clusters.

Affiliation splitting is a subsequent phase, not implemented here.

Usage:
  .venv/bin/python analysis/hca_match.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from nameparser import HumanName

sys.path.insert(0, str(Path(__file__).parent.parent))

from util import load_config
from util.name_util import clean_name, last_norm, fi
from analysis.name_cluster import build_name_index, assign_name_cluster_hashes


# ── load ──────────────────────────────────────────────────────────────────────

def load_hca(
    path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[int, HumanName]]:
    """
    Load hca_authors.parquet; apply per-field threshold; deduplicate to one row
    per author_idx for name parsing and clustering.

    Threshold: per field, keep rows where n_hcw >= 90th-percentile of n_hcw.
    This retains the top ~10% most prolific HCW authors per field.

    Returns:
      hca      — full filtered rows, row_idx index, author_idx column
      hca_uniq — one row per author_idx, author_idx index, with last_norm/fi
      row_hn   — {author_idx: HumanName}, keyed to hca_uniq index
    """
    hca = pd.read_parquet(path)   # row_idx is a column

    # per-field 90th-percentile threshold
    thresh = (
        hca.groupby('field_idx')['n_hcw']
           .quantile(0.9)
           .apply(lambda x: max(1, int(x)))
           .rename('thresh')
    )
    print('  Per-field n_hcw thresholds (90th pct):')
    print(f"    {'field_idx':>9}  {'thresh':>6}  {'kept':>8}  {'total':>8}")
    for fid, t in thresh.items():
        total = (hca['field_idx'] == fid).sum()
        kept  = ((hca['field_idx'] == fid) & (hca['n_hcw'] >= t)).sum()
        print(f"    {fid:>9}  {t:>6}  {kept:>8,}  {total:>8,}")

    hca = hca.merge(thresh, on='field_idx')
    hca = hca[hca['n_hcw'] >= hca['thresh']].drop(columns='thresh')
    hca = hca.set_index('row_idx')

    # one row per author_idx for name parsing (same display_name across fields)
    hca_uniq = (
        hca.drop_duplicates(subset='author_idx')
           .reset_index(drop=True)
           .set_index('author_idx')
    )

    unique_names = hca_uniq['display_name'].dropna().unique()
    by_name: dict[str, HumanName] = {n: HumanName(clean_name(n)) for n in unique_names}

    dn_col = hca_uniq['display_name'].fillna('')
    last_norm_map: dict[str, str] = {n: last_norm(hn.last) for n, hn in by_name.items()}
    fi_map:        dict[str, str] = {n: fi(hn.first)       for n, hn in by_name.items()}
    hca_uniq['last_norm'] = dn_col.map(last_norm_map).fillna('')
    hca_uniq['fi']        = dn_col.map(fi_map).fillna('')

    row_hn: dict[int, HumanName] = {
        idx: by_name[dn]
        for idx, dn in dn_col.items()
        if dn in by_name
    }
    return hca, hca_uniq, row_hn


# ── Phase 1b: name parse diagnostic ──────────────────────────────────────────

def name_parse_diag(hca_uniq: pd.DataFrame, row_hn: dict[int, HumanName]) -> None:
    """
    Flag rows where HumanName found a title or suffix.
    hca_uniq is indexed by author_idx, matching row_hn keys.
    Deduplicates by display_name; caps output at 100 rows.
    """
    seen: set[str] = set()
    flags = []
    total = len(hca_uniq)
    for i, (idx, dn) in enumerate(zip(hca_uniq.index.tolist(), hca_uniq['display_name'])):
        if i % 100_000 == 0:
            print(f'    {i:>8,} / {total:,}  ({len(seen):,} unique names)', flush=True)
        if idx not in row_hn or dn in seen:
            continue
        seen.add(dn)
        hn = row_hn[idx]
        issues = []
        if hn.title:  issues.append(f"title={hn.title!r}")
        if hn.suffix: issues.append(f"suffix={hn.suffix!r}")
        if issues:
            flags.append((idx, dn, hn, ', '.join(issues)))

    display = sorted(
        [(i, d, h, s) for i, d, h, s in flags if h.title.lower().strip('.') != 'md'],
        key=lambda x: x[2].title.lower()
    )
    print(f"\nName parse flags: {len(flags)} distinct display_names "
          f"({len(flags) - len(display)} Md. excluded)\n")
    print(f"  {'#':<10} {'DISPLAY_NAME':<34} {'HN.FIRST':<16} {'HN.MID':<10} ISSUES")
    print('  ' + '-' * 110)
    for author_idx, dn, hn, issues in display[:100]:
        print(f"  {author_idx:<10} {dn:<34} {hn.first:<16} {hn.middle:<10} {issues}")
    if len(display) > 100:
        print(f"  ... ({len(display) - 100} more not shown)")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    paths = load_config()
    hca_path = paths.working / 'hca_authors.parquet'

    print('Phase 1: loading hca_authors...')
    hca, hca_uniq, row_hn = load_hca(hca_path)
    print(f'\n  {len(hca):,} rows after threshold  '
          f'({hca["author_idx"].nunique():,} distinct author_idx)')
    print(f'  {len(hca_uniq):,} unique authors for clustering')

    print('\nPhase 1b: name parse diagnostic...')
    name_parse_diag(hca_uniq, row_hn)

    print('\nPhase 1b continued: partitioning name forms and assigning cluster keys...')
    name_idx = build_name_index(row_hn)
    print(f'  {len(name_idx):,} distinct family names — clustering...', flush=True)
    hca_uniq, unresolved_name = assign_name_cluster_hashes(hca_uniq, name_idx)
    print(f'  done')

    hca = hca.join(hca_uniq[['cluster_hash']], on='author_idx')

    print(f'  {len(unresolved_name):,} unresolved name clusters (2+ authors, non-identical first names)\n')
    for family, cluster in sorted(unresolved_name[:20], key=lambda x: x[0]):
        print(f"  {family}")
        for author_idx, hn in sorted(cluster.items()):
            print(f"    {author_idx:12d}  {hn.first} {hn.middle}")
    if len(unresolved_name) > 20:
        print(f"  ... ({len(unresolved_name) - 20} more not shown)")


if __name__ == '__main__':
    main()
