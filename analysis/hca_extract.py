"""
hca_extract.py — Highly Cited Authors (HCA) extraction across OA fields.

HCA definition:
  For each OA field f, an author is HCA if they rank in the top HCA_count(f)
  authors by distinct HCW authored in that field, where:

    HCA_count(f) = HCA_TOT * SQRT(HCW_TOT(f)) / sum_f(SQRT(HCW_TOT(f)))

  This compresses field-size dominance: slots scale as SQRT(field size), not linearly.
  HCW = top 1% of works by intra-field citation count per (field_idx, publication_year).

Inputs:
  WORKING/enclave_hcw_{window}_{label}.parquet
  OPENALEX/parquet/authorships/*.parquet
  OPENALEX/parquet/authors/*.parquet

Outputs:
  WORKING/hca_{window}_{label}.parquet
  stdout: top-4 HCA per field

Parquet schema (one row per HCA × field):
  field_idx, author_idx, n_hcw, rank_in_field, hca_count_f,
  display_name, h_index, works_count, cited_by_count, orcid,
  inst_name, inst_country   (most frequent institution in HCW authorships for this field)

Usage:
  .venv/bin/python analysis/hca_extract.py
  .venv/bin/python analysis/hca_extract.py --hca-tot 10000 --window 2020_2024 --label baseline
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

HCA_TOT_DEFAULT = 25_000


# ── allocation ────────────────────────────────────────────────────────────────

def compute_hca_allocations(author_tots: pd.Series, hca_tot: int) -> pd.Series:
    """
    HCA slot count per field (int), keyed by field_idx.  Mirrors Clarivate's recipe:

      HCA_count(f) = hca_tot * SQRT(N_authors(f)) / sum_f(SQRT(N_authors(f)))

    where N_authors(f) = distinct authors who appear on at least one HCW in field f.
    Clarivate uses the same √(author-pool) rule with their ESI HCP author counts.
    Rounding is done per-field; total may differ from hca_tot by ≤ n_fields.
    """
    sqrt_tots = np.sqrt(author_tots)
    return (hca_tot * sqrt_tots / sqrt_tots.sum()).round().astype(int)


# ── author HCW counts from authorships scan ───────────────────────────────────

MAX_AUTHORS = 30   # mirror Clarivate: exclude HCW with > 30 authors


def count_author_hcw(hcw: pd.DataFrame, auth_glob: str, inst_path: str) -> pd.DataFrame:
    """
    Single DuckDB pass over all authorships, then in-memory filter.

    Mirrors Clarivate: HCW with more than MAX_AUTHORS distinct authors are
    excluded from the HCA count.  Credit fractionation already handles these
    in the spectral ranking, but awarding HCA credit for a 1/5000 contribution
    strains reason.

    Returns df: field_idx, author_idx, n_hcw, inst_name, inst_country
    where inst_* is the modal institution for that (author, field) across their HCW.
    """
    con = duckdb.connect()
    con.register('_hcw', hcw[['field_idx', 'work_idx']])

    con.execute(f"""
        CREATE TEMP TABLE _inst_lookup AS
        SELECT institution_idx, display_name AS institution_name
        FROM '{inst_path}'
    """)

    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _auth_hcw_raw AS
        SELECT h.field_idx,
               a.author_idx,
               a.work_idx,
               il.institution_name,
               a.country_code
        FROM parquet_scan('{auth_glob}') a
        JOIN _hcw h ON h.work_idx = a.work_idx
        LEFT JOIN _inst_lookup il ON il.institution_idx = a.institution_idx
        WHERE a.author_idx IS NOT NULL
    """)
    n_raw = (con.execute('SELECT COUNT(*) FROM _auth_hcw_raw').fetchone() or (0,))[0]
    print(f'  authorships × HCW (raw): {n_raw:,} rows  [{time.time()-t0:.1f}s]')

    # works with ≤ MAX_AUTHORS distinct authors
    con.execute(f"""
        CREATE TEMP TABLE _keep_works AS
        SELECT work_idx
        FROM (
            SELECT work_idx, COUNT(DISTINCT author_idx) AS n_authors
            FROM _auth_hcw_raw
            GROUP BY work_idx
        )
        WHERE n_authors <= {MAX_AUTHORS}
    """)
    n_keep = (con.execute('SELECT COUNT(*) FROM _keep_works').fetchone() or (0,))[0]
    n_hcw_total = hcw['work_idx'].nunique()
    print(f'  HCW works kept (≤{MAX_AUTHORS} authors): {n_keep:,} / {n_hcw_total:,}'
          f'  ({(n_hcw_total - n_keep):,} excluded)')

    con.execute("""
        CREATE TEMP TABLE _auth_hcw AS
        SELECT r.*
        FROM _auth_hcw_raw r
        JOIN _keep_works k ON k.work_idx = r.work_idx
    """)
    n = con.execute('SELECT COUNT(*) FROM _auth_hcw').fetchone()[0]
    print(f'  authorships × HCW (filtered): {n:,} rows')

    # n_hcw per (field_idx, author_idx)
    con.execute("""
        CREATE TEMP TABLE _counts AS
        SELECT field_idx, author_idx, COUNT(DISTINCT work_idx) AS n_hcw
        FROM _auth_hcw
        GROUP BY field_idx, author_idx
    """)

    # modal institution: most frequent institution_name per (field_idx, author_idx)
    con.execute("""
        CREATE TEMP TABLE _inst_mode AS
        WITH _ic AS (
            SELECT field_idx, author_idx, institution_name, country_code,
                   COUNT(*) AS n
            FROM _auth_hcw
            WHERE institution_name IS NOT NULL
            GROUP BY field_idx, author_idx, institution_name, country_code
        ),
        _ir AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY field_idx, author_idx
                       ORDER BY n DESC
                   ) AS rn
            FROM _ic
        )
        SELECT field_idx, author_idx, institution_name AS inst_name, country_code AS inst_country
        FROM _ir WHERE rn = 1
    """)

    result = con.execute("""
        SELECT c.field_idx, c.author_idx, c.n_hcw,
               m.inst_name, m.inst_country
        FROM _counts c
        LEFT JOIN _inst_mode m USING (field_idx, author_idx)
    """).df()

    con.close()
    return result


# ── apply allocation thresholds ───────────────────────────────────────────────

def apply_thresholds(
    counts: pd.DataFrame,
    allocations: pd.Series,
) -> pd.DataFrame:
    """
    Rank authors within each field and retain top HCA_count(f).

    Ties at the cut-off are broken arbitrarily by ROW_NUMBER logic (first occurrence).
    Returns df with rank_in_field and hca_count_f added; only HCA rows retained.
    """
    counts = counts.copy()
    counts['rank_in_field'] = (
        counts.groupby('field_idx')['n_hcw']
        .rank(method='first', ascending=False)
        .astype(int)
    )
    counts['hca_count_f'] = counts['field_idx'].map(allocations)
    hca = counts[counts['rank_in_field'] <= counts['hca_count_f']].copy()
    return hca.reset_index(drop=True)


# ── author metadata from authors parquet ─────────────────────────────────────

def fetch_author_metadata(author_ids: list[int], au_glob: str) -> pd.DataFrame:
    """
    Returns df: author_idx, display_name, h_index, works_count, cited_by_count, orcid
    """
    con = duckdb.connect()
    con.register('_aid', pd.DataFrame({'author_idx': author_ids}))

    t0 = time.time()
    meta = con.execute(f"""
        SELECT
            a.author_idx,
            a.display_name,
            a.h_index,
            a.works_count,
            a.cited_by_count,
            a.orcid
        FROM parquet_scan('{au_glob}') a
        JOIN _aid t ON a.author_idx = t.author_idx
    """).df()
    con.close()
    print(f'  author metadata: {len(meta):,} rows  [{time.time()-t0:.1f}s]')
    return meta


# ── console display ───────────────────────────────────────────────────────────

def print_top_n(hca: pd.DataFrame, top_n: int = 4) -> None:
    sep = '─' * 110
    print(f'\nTop-{top_n} HCA per field  (n_hcw = distinct HCW in field)')
    print(sep)
    for fid in sorted(hca['field_idx'].unique()):
        fname = FIELD_NAMES.get(fid, str(fid))
        grp = hca[hca['field_idx'] == fid].sort_values('rank_in_field').head(top_n)
        alloc = int(grp['hca_count_f'].iloc[0])
        print(f'\n  {fid:>3}  {fname}  [HCA_count={alloc}]')
        print(f'  {"rnk":>4}  {"n_hcw":>6}  {"name":<40}  {"institution":<35}  {"cc":>3}  {"h":>5}')
        print(f'  {"─"*4}  {"─"*6}  {"─"*40}  {"─"*35}  {"─"*3}  {"─"*5}')
        for _, row in grp.iterrows():
            name  = str(row.get('display_name') or '—')[:40]
            inst  = str(row.get('inst_name')    or '—')[:35]
            cc    = str(row.get('inst_country') or '—')[:3]
            h     = f'{int(row["h_index"])}' if pd.notna(row.get('h_index')) else '—'
            print(f'  {int(row["rank_in_field"]):>4}  {int(row["n_hcw"]):>6}  '
                  f'{name:<40}  {inst:<35}  {cc:>3}  {h:>5}')
    print(f'\n{sep}')


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window',  default='2020_2024')
    parser.add_argument('--label',   default='baseline')
    parser.add_argument('--hca-tot', type=int, default=HCA_TOT_DEFAULT)
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()

    hcw_path  = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    out_path  = paths.working / f'hca_{args.window}_{args.label}.parquet'
    oa        = paths.openalex / 'parquet'
    auth_glob = str(oa / 'authorships'   / '*.parquet')
    inst_path = str(oa / 'institutions.parquet')
    au_glob   = str(oa / 'authors'       / '*.parquet')

    if not hcw_path.exists():
        print(f'ERROR: {hcw_path} not found'); sys.exit(1)

    # ── 1. HCW ───────────────────────────────────────────────────────────────
    print('Loading HCW...')
    hcw = pd.read_parquet(hcw_path, columns=['field_idx', 'work_idx'])
    print(f'  {len(hcw):,} HCW rows  |  {hcw["field_idx"].nunique()} fields  '
          f'|  {hcw["work_idx"].nunique():,} distinct works')

    # ── 2. Author HCW counts (full authorship scan) ───────────────────────────
    print('\nCounting HCW per author (authorship scan)...')
    t0 = time.time()
    counts = count_author_hcw(hcw, auth_glob, inst_path)
    print(f'  {len(counts):,} (field × author) pairs  [{time.time()-t0:.1f}s]')

    # ── 3. Allocations from author-pool size (Clarivate recipe) ──────────────
    author_tots = counts.groupby('field_idx')['author_idx'].count()
    allocations = compute_hca_allocations(author_tots, args.hca_tot)
    print(f'\nHCA_TOT={args.hca_tot}  →  sum of allocations={allocations.sum():,}')
    print(f'  N_authors per field (√ used as allocation basis):')
    for fid in sorted(int(k) for k in author_tots.index):
        n = int(author_tots[fid])
        print(f'    {fid:>3}  {FIELD_NAMES.get(fid,str(fid)):<14}  '
              f'N_authors={n:>7,}  √={np.sqrt(n):>7.1f}  '
              f'HCA_count={allocations[fid]:>5}')

    # ── 4. Apply thresholds ───────────────────────────────────────────────────
    print('\nApplying HCA thresholds...')
    hca = apply_thresholds(counts, allocations)
    print(f'  {len(hca):,} HCA rows  |  '
          f'{hca["field_idx"].nunique()} fields  |  '
          f'{hca["author_idx"].nunique():,} distinct authors')

    # ── 5. Author metadata ────────────────────────────────────────────────────
    print('\nFetching author metadata...')
    author_ids = hca['author_idx'].dropna().astype(int).unique().tolist()
    meta = fetch_author_metadata(author_ids, au_glob)
    hca = hca.merge(meta, on='author_idx', how='left')

    # ── 6. Save ───────────────────────────────────────────────────────────────
    col_order = [
        'field_idx', 'author_idx', 'n_hcw', 'rank_in_field', 'hca_count_f',
        'display_name', 'h_index', 'works_count', 'cited_by_count', 'orcid',
        'inst_name', 'inst_country',
    ]
    hca = hca[col_order].sort_values(['field_idx', 'rank_in_field'])
    hca.to_parquet(out_path, index=False)
    print(f'\nSaved: {out_path}  ({len(hca):,} rows)')

    # ── 7. Console display ────────────────────────────────────────────────────
    print_top_n(hca, top_n=4)


if __name__ == '__main__':
    main()
