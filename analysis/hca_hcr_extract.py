"""
hca_hcr_extract.py — HCA extraction using full OA citation counts for HCR comparison.

Identical to hca_extract.py EXCEPT: HCW are identified using ALL OA citations
(references/*.parquet, 3B rows) rather than retained-corpus-only citations.
This aligns the citation counting window with Clarivate's approach: any citing
work counts, not just those from retained sources.

  hca_extract.py     : n_intra = citations from retained-source corpus works only
  hca_hcr_extract.py : n_total = all OA citations to each candidate work

HCW candidates are still restricted to works from retained sources published
2014-2023 (same as the enclave HCA), so v-scores are defined for all candidates.

Output: WORKING/hca_hcr_{window}_{label}.parquet  (same schema as hca_*.parquet)
        stdout: top-4 HCA per field

Usage:
  .venv/bin/python analysis/hca_hcr_extract.py
  .venv/bin/python analysis/hca_hcr_extract.py --hca-tot 25000 --window 2020_2024 --label baseline
"""

import sys
import time
import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings, FIELD_NAMES

# reuse pure-logic helpers from hca_extract
from hca_extract import (
    compute_hca_allocations,
    count_author_hcw,
    apply_thresholds,
    fetch_author_metadata,
    print_top_n,
    HCA_TOT_DEFAULT,
)

YEAR_LO_HCW = 2014
YEAR_HI_HCW = 2023


# ── HCW via full OA references ─────────────────────────────────────────────────

def identify_hcw_full_cites(
    fw_path: str,
    refs_glob: str,
    hcw_pct: int = 99,
) -> pd.DataFrame:
    """
    Identify HCW using ALL OA citations (not just retained-corpus citers).

    Candidate works: ALL works in flat_works published 2014-2023.
    No source retention filter — mirrors ESI's approach (any indexed paper is a candidate).
    Citation count: every row in references/*.parquet where cited_idx is a candidate.

    Returns df: field_idx, work_idx, publication_year, source_idx, n_total, year_threshold
    (only HCW rows, i.e. n_total >= year_threshold).
    """
    con = duckdb.connect()

    # ── candidate works: all sources in flat_works, 2014-2023 ────────────────
    print('  Scanning flat_works for candidates (all sources)...')
    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _cands AS
        SELECT DISTINCT
            fw.field_idx,
            fw.work_idx,
            fw.source_idx,
            fw.publication_year
        FROM parquet_scan('{fw_path}') fw
        WHERE fw.publication_year BETWEEN {YEAR_LO_HCW} AND {YEAR_HI_HCW}
    """)
    n_cands = con.execute('SELECT COUNT(DISTINCT work_idx) FROM _cands').fetchone()[0]
    print(f'  candidate works: {n_cands:,}  [{time.time()-t0:.1f}s]')

    # ── count ALL OA citations to each candidate work ─────────────────────────
    print('  Counting all OA citations (3B-row references scan)...')
    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _cite_counts AS
        SELECT cited_idx AS work_idx, COUNT(*) AS n_total
        FROM (
            SELECT UNNEST(cited_list) AS cited_idx
            FROM parquet_scan('{refs_glob}')
        ) sub
        WHERE cited_idx IN (SELECT DISTINCT work_idx FROM _cands)
        GROUP BY cited_idx
    """)
    n_cited = con.execute('SELECT COUNT(*) FROM _cite_counts').fetchone()[0]
    print(f'  works with ≥1 citation: {n_cited:,}  [{time.time()-t0:.1f}s]')

    # ── join counts back to (field_idx, work_idx) ─────────────────────────────
    rw = con.execute("""
        SELECT c.field_idx, c.work_idx, c.source_idx, c.publication_year,
               COALESCE(cc.n_total, 0) AS n_total
        FROM _cands c
        LEFT JOIN _cite_counts cc ON cc.work_idx = c.work_idx
    """).df()
    con.close()
    print(f'  (field × work) rows: {len(rw):,}')

    # ── top-1% threshold per (field_idx, publication_year) ───────────────────
    thresh = (
        rw.groupby(['field_idx', 'publication_year'])['n_total']
        .quantile(hcw_pct / 100.0)
        .rename('year_threshold')
        .reset_index()
    )
    rw = rw.merge(thresh, on=['field_idx', 'publication_year'], how='left')
    hcw = rw[rw['n_total'] >= rw['year_threshold']].copy()

    n_hcw = hcw['work_idx'].nunique()
    print(f'  HCW (top {100-hcw_pct}%, full cites): {len(hcw):,} rows  '
          f'| {n_hcw:,} distinct works  | {hcw["field_idx"].nunique()} fields')
    return hcw[['field_idx', 'work_idx', 'publication_year', 'source_idx',
                'n_total', 'year_threshold']].reset_index(drop=True)


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window',  default='2020_2024')
    parser.add_argument('--label',   default='baseline')
    parser.add_argument('--hca-tot', type=int, default=HCA_TOT_DEFAULT)
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()

    fw_path   = str(paths.working   / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    refs_glob = str(paths.openalex  / 'parquet' / 'references' / '*.parquet')
    auth_glob = str(paths.openalex  / 'parquet' / 'authorships'      / '*.parquet')
    inst_path = str(paths.openalex  / 'parquet' / 'institutions.parquet')
    au_glob   = str(paths.openalex  / 'parquet' / 'authors'           / '*.parquet')
    out_path  = paths.working / f'hca_hcr_{args.window}_{args.label}.parquet'

    # ── 1. HCW via full OA citation counts ───────────────────────────────────
    print('Identifying HCW (full OA citations, all sources)...')
    hcw = identify_hcw_full_cites(fw_path, refs_glob)

    # ── 3. Author HCW counts (authorship scan, >30-author filter) ────────────
    print('\nCounting HCW per author (authorship scan)...')
    t0 = time.time()
    counts = count_author_hcw(hcw, auth_glob, inst_path)
    print(f'  {len(counts):,} (field × author) pairs  [{time.time()-t0:.1f}s]')

    # ── 4. Allocations ────────────────────────────────────────────────────────
    author_tots = counts.groupby('field_idx')['author_idx'].count()
    allocations = compute_hca_allocations(author_tots, args.hca_tot)
    print(f'\nHCA_TOT={args.hca_tot}  →  sum of allocations={allocations.sum():,}')
    print(f'  N_authors per field (√ used as allocation basis):')
    for fid in sorted(int(k) for k in author_tots.index):
        n = int(author_tots[fid])
        print(f'    {fid:>3}  {FIELD_NAMES.get(fid,str(fid)):<14}  '
              f'N_authors={n:>7,}  √={np.sqrt(n):>7.1f}  '
              f'HCA_count={allocations[fid]:>5}')

    # ── 5. Apply thresholds ───────────────────────────────────────────────────
    print('\nApplying HCA thresholds...')
    hca = apply_thresholds(counts, allocations)
    print(f'  {len(hca):,} HCA rows  |  '
          f'{hca["field_idx"].nunique()} fields  |  '
          f'{hca["author_idx"].nunique():,} distinct authors')

    # ── 6. Author metadata ────────────────────────────────────────────────────
    print('\nFetching author metadata...')
    author_ids = hca['author_idx'].dropna().astype(int).unique().tolist()
    meta = fetch_author_metadata(author_ids, au_glob)
    hca = hca.merge(meta, on='author_idx', how='left')

    # ── 7. Save ───────────────────────────────────────────────────────────────
    col_order = [
        'field_idx', 'author_idx', 'n_hcw', 'rank_in_field', 'hca_count_f',
        'display_name', 'h_index', 'works_count', 'cited_by_count', 'orcid',
        'inst_name', 'inst_country',
    ]
    hca = hca[col_order].sort_values(['field_idx', 'rank_in_field'])
    hca.to_parquet(out_path, index=False)
    print(f'\nSaved: {out_path}  ({len(hca):,} rows)')

    # ── 8. Console display ────────────────────────────────────────────────────
    print_top_n(hca, top_n=4)


if __name__ == '__main__':
    main()
