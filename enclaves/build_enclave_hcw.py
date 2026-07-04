"""
build_enclave_hcw.py — Stage 1: Highly Cited Work detection per OA field.

For each OA field (11–36), identifies Highly Cited Works (HCW) among works whose
source is retained in the field's baseline spectral ranking.

Definition:
  - HCW: a retained work whose intra-field citation count (citations from other
    retained works in the same field) is at or above the 99th percentile for its
    publication year and field.
  - Intra-field: both citer and cited must have a retained source in the same field.

Three measurements per HCW:
  source_v    : spectral influence of the publishing source
  mean_inst_v : mean v across distinct affiliated institutions
  gap         : source_v − mean_inst_v

Publication year range: YEAR_LO_HCW–YEAR_HI_HCW (2014–2023); 10-year window.

Strategy for scale (153M flat_works, 358M corpus_references):
  Phase 1 — in-memory DuckDB TEMP TABLEs (two flat_works scans, one corpus_references scan):
    - retained_works  (field_idx, work_idx, source_idx, publication_year) — ~5M rows
    - intra_counts    (field_idx, work_idx, n_intra) — single corpus_references pass, all fields
    - work_inst       (work_idx, institution_idx) — DISTINCT pairs for retained works
  Phase 2 — pandas threshold + v scoring (small)

Output: WORKING/enclave_hcw_{window}_{label}.parquet

Usage:
  .venv/bin/python enclaves/build_enclave_hcw.py
  .venv/bin/python enclaves/build_enclave_hcw.py --window 2020_2024 --label baseline
"""

import sys
import time
import argparse
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings, FIELD_NAMES, guard

HCW_PCT = 99
YEAR_LO     = 2000  # flat_works corpus start
YEAR_LO_HCW = 2014  # HCW analysis start: 10-year window 2014–2023
YEAR_HI_HCW = 2023


# ── pure-logic functions (no file I/O; testable with synthetic DataFrames) ────

def compute_hcw_flags(rw: pd.DataFrame, hcw_pct: int = HCW_PCT) -> pd.DataFrame:
    """
    Add year_threshold and is_hcw columns to retained-works DataFrame.

    rw must have: field_idx, work_idx, publication_year, n_intra.
    Threshold is the hcw_pct-th percentile of n_intra per (field_idx, publication_year).
    Returns a copy.
    """
    thresh = (
        rw.groupby(['field_idx', 'publication_year'])['n_intra']
        .quantile(hcw_pct / 100.0)
        .rename('year_threshold')
        .reset_index()
    )
    out = rw.merge(thresh, on=['field_idx', 'publication_year'], how='left')
    out['is_hcw'] = out['n_intra'] >= out['year_threshold']
    return out


def count_intra_field_citations(
    rw: pd.DataFrame,
    cr: pd.DataFrame,
) -> pd.DataFrame:
    """
    Count intra-field citations using in-memory DuckDB (for tests / small data).

    rw : columns field_idx (int), work_idx (int)
    cr : columns citer_idx (int), cited_idx (int)

    Returns DataFrame with columns: field_idx, cited_idx, n_intra.
    Only rows with n_intra >= 1 are returned.
    """
    db = duckdb.connect()
    db.register('_rw', rw[['field_idx', 'work_idx']].copy())
    db.register('_cr', cr[['citer_idx', 'cited_idx']].copy())
    result = db.execute("""
        SELECT rw_d.field_idx,
               rw_d.work_idx  AS cited_idx,
               COUNT(*)       AS n_intra
        FROM _cr cr
        JOIN _rw rw_r ON rw_r.work_idx  = cr.citer_idx
        JOIN _rw rw_d ON rw_d.work_idx  = cr.cited_idx
                      AND rw_d.field_idx = rw_r.field_idx
        GROUP BY rw_d.field_idx, rw_d.work_idx
    """).df()
    db.close()
    return result


def compute_mean_inst_v(
    hcw: pd.DataFrame,
    fw_inst: pd.DataFrame,
    inst_v: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute mean institution v per HCW using DISTINCT (work, institution) pairs.

    hcw     : columns field_idx, work_idx
    fw_inst : columns work_idx, institution_idx  (already DISTINCT)
    inst_v  : columns field_idx, institution_idx, v

    Returns DataFrame with columns: field_idx, work_idx, mean_inst_v.
    Only HCW with ≥1 ranked institution are returned.
    """
    db = duckdb.connect()
    db.register('_hcw',     hcw[['field_idx', 'work_idx']].copy())
    db.register('_fw_inst', fw_inst[['work_idx', 'institution_idx']].copy())
    db.register('_inst_v',  inst_v[['field_idx', 'institution_idx', 'v']].copy())
    result = db.execute("""
        SELECT h.field_idx,
               h.work_idx,
               AVG(iv.v) AS mean_inst_v
        FROM _hcw h
        JOIN _fw_inst fi ON fi.work_idx         = h.work_idx
        JOIN _inst_v  iv ON iv.field_idx         = h.field_idx
                         AND iv.institution_idx  = fi.institution_idx
        GROUP BY h.field_idx, h.work_idx
    """).df()
    db.close()
    return result


# ── in-memory scratch database ────────────────────────────────────────────────

def build_scratch(
    fw_path: str,
    cr_path: str,
    src_v_all: pd.DataFrame,
) -> duckdb.DuckDBPyConnection:
    """
    Build an in-memory DuckDB with TEMP TABLEs using a single flat_works scan.

    _fw_base        — single flat_works scan (retained works + institution_idx)
    retained_works  — derived from _fw_base; one row per (field_idx, work_idx)
    retained_citers — MAX source_v per citer work across all fields
    intra_counts    — single corpus_references scan (all fields at once)
    work_inst       — derived from _fw_base; no second parquet scan

    Returns the open connection; caller must close it.
    """
    con = duckdb.connect()
    con.register('_src_v', src_v_all)

    print('  Building _fw_base (single flat_works scan)...')
    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _fw_base AS
        SELECT DISTINCT
            fw.field_idx,
            fw.work_idx,
            fw.source_idx,
            fw.publication_year,
            sv.v            AS source_v,
            fw.institution_idx
        FROM parquet_scan('{fw_path}') fw
        JOIN _src_v sv
            ON  sv.field_idx  = fw.field_idx
            AND sv.source_idx = fw.source_idx
        WHERE fw.publication_year BETWEEN {YEAR_LO_HCW} AND {YEAR_HI_HCW}
    """)
    print(f'  _fw_base done  [{time.time()-t0:.1f}s]')

    con.execute("""
        CREATE TEMP TABLE retained_works AS
        SELECT DISTINCT field_idx, work_idx, source_idx, publication_year, source_v
        FROM _fw_base
    """)
    n = con.execute("SELECT COUNT(*) FROM retained_works").fetchone()[0]
    print(f'  retained_works: {n:,} rows')

    # One row per citer work with its best (MAX) source_v across all fields.
    # Avoids double-counting when a citer is retained in multiple fields.
    con.execute("""
        CREATE TEMP TABLE retained_citers AS
        SELECT work_idx, MAX(source_v) AS source_v
        FROM retained_works
        GROUP BY work_idx
    """)

    print('  Counting cross-field citations (single corpus_references scan)...')
    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE intra_counts AS
        SELECT
            rw_d.field_idx,
            rw_d.work_idx,
            COUNT(*)                                        AS n_intra,
            AVG(rc.source_v)                               AS mean_citer_v,
            COUNT(CASE WHEN rc.source_v > 1 THEN 1 END)   AS n_citer_hi,
            COUNT(CASE WHEN rc.source_v < 1 THEN 1 END)   AS n_citer_lo
        FROM parquet_scan('{cr_path}') cr
        JOIN retained_citers rc   ON rc.work_idx  = cr.citer_idx
        JOIN retained_works  rw_d ON rw_d.work_idx = cr.cited_idx
        GROUP BY rw_d.field_idx, rw_d.work_idx
    """)
    n = con.execute("SELECT COUNT(*) FROM intra_counts").fetchone()[0]
    print(f'  intra_counts: {n:,} rows  [{time.time()-t0:.1f}s]')

    con.execute("""
        CREATE TEMP TABLE work_inst AS
        SELECT DISTINCT work_idx, institution_idx
        FROM _fw_base
        WHERE institution_idx IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM work_inst").fetchone()[0]
    print(f'  work_inst: {n:,} rows')

    return con


# ── pipeline orchestration ────────────────────────────────────────────────────

def _load_all_rankings(
    working: Path, window: str, label: str, all_fields
) -> tuple[pd.DataFrame, pd.DataFrame]:
    src_rows, inst_rows = [], []
    for fid in all_fields:
        rk_path = working / f'rankings_{fid}_{window}_{label}.parquet'
        if not rk_path.exists():
            continue
        df = pd.read_parquet(rk_path, columns=['unit_idx', 'unit_type', 'v'])
        df['field_idx'] = fid
        src_rows.append(
            df[df['unit_type'] == 'S'][['field_idx', 'unit_idx', 'v']]
            .rename(columns={'unit_idx': 'source_idx'})
        )
        inst_rows.append(
            df[df['unit_type'] == 'U'][['field_idx', 'unit_idx', 'v']]
            .rename(columns={'unit_idx': 'institution_idx'})
        )
    return (
        pd.concat(src_rows,  ignore_index=True),
        pd.concat(inst_rows, ignore_index=True),
    )


def build_enclave_hcw(window: str, label: str, paths, settings) -> pd.DataFrame:
    working = paths.working
    fw_path = str(working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    # ── 1. Rankings ──────────────────────────────────────────────────────────
    print('Loading retained units...')
    src_v_all, inst_v_all = _load_all_rankings(working, window, label, settings.all_fields)
    print(f'  {len(src_v_all):,} (field, source) pairs  '
          f'{len(inst_v_all):,} (field, institution) pairs')

    # ── 2. In-memory scratch: retained_works, intra_counts, work_inst ────────
    print('\nPhase 1 — in-memory scratch tables')
    con = build_scratch(fw_path, cr_path, src_v_all)

    # ── 3. Pull retained_works + intra_counts into pandas ────────────────────
    print('\nPhase 2 — assembling HCW table')
    rw = con.execute("""
        SELECT rw.field_idx, rw.work_idx, rw.publication_year, rw.source_idx,
               rw.source_v,
               COALESCE(ic.n_intra, 0)      AS n_intra,
               ic.mean_citer_v,
               COALESCE(ic.n_citer_hi, 0)   AS n_citer_hi,
               COALESCE(ic.n_citer_lo, 0)   AS n_citer_lo
        FROM retained_works rw
        LEFT JOIN intra_counts ic
               ON ic.field_idx = rw.field_idx AND ic.work_idx = rw.work_idx
    """).df()
    print(f'  retained works: {len(rw):,}')

    # ── 4. HCW flag ──────────────────────────────────────────────────────────
    rw_flagged = compute_hcw_flags(rw, HCW_PCT)
    hcw = rw_flagged[rw_flagged['is_hcw']].copy()
    print(f'  HCW: {len(hcw):,} across {hcw["field_idx"].nunique()} fields')

    # ── 5. Mean institution v ────────────────────────────────────────────────
    fw_inst = con.execute("SELECT work_idx, institution_idx FROM work_inst").df()
    con.close()

    miv = compute_mean_inst_v(hcw[['field_idx', 'work_idx']], fw_inst, inst_v_all)
    print(f'  HCW with ≥1 ranked institution: {len(miv):,}')

    # ── 7. Assemble output ───────────────────────────────────────────────────
    hcw = hcw.merge(miv, on=['field_idx', 'work_idx'], how='left')
    hcw['gap'] = hcw['source_v'] - hcw['mean_inst_v']

    # ── 8. Attach titles from flat_works (master table carries title directly) ─
    t0 = time.time()
    tcon = duckdb.connect()
    tcon.register('_hcw', hcw[['work_idx']].drop_duplicates())
    titles = tcon.execute(f"""
        SELECT DISTINCT w.work_idx, w.title
        FROM parquet_scan('{fw_path}') w
        JOIN _hcw h ON h.work_idx = w.work_idx
    """).df()
    tcon.close()
    hcw = hcw.merge(titles, on='work_idx', how='left')
    print(f'  titles attached: {hcw["title"].notna().sum():,} / {len(hcw):,}  [{time.time()-t0:.1f}s]')

    out_cols = [
        'field_idx', 'work_idx', 'publication_year', 'source_idx',
        'n_intra', 'year_threshold', 'source_v', 'mean_citer_v',
        'n_citer_hi', 'n_citer_lo', 'mean_inst_v', 'gap', 'title',
    ]
    return hcw[out_cols].reset_index(drop=True)


# ── summary and entry point ───────────────────────────────────────────────────

def print_summary(df: pd.DataFrame) -> None:
    enclave_mask = (df['source_v'] < 1) & (df['mean_citer_v'] < 1)
    lo_hi_mask   = (df['source_v'] < 1) & (df['mean_citer_v'] > 1)

    summary = (
        df.groupby('field_idx')
        .agg(
            n_hcw         = ('work_idx',      'count'),
            median_src_v  = ('source_v',      'median'),
            median_citer_v= ('mean_citer_v',  lambda x: x.dropna().median()),
            median_inst_v = ('mean_inst_v',   lambda x: x.dropna().median()),
        )
        .reset_index()
    )
    summary['n_enclave'] = df[enclave_mask].groupby('field_idx').size().reindex(summary['field_idx']).fillna(0).astype(int).values
    summary['n_lo_hi']   = df[lo_hi_mask].groupby('field_idx').size().reindex(summary['field_idx']).fillna(0).astype(int).values
    summary['enc_rate']  = summary['n_enclave'] / summary['n_hcw']

    print(f'\n{"fid":>4}  {"field":>12}  {"n_hcw":>8}  '
          f'{"enclave":>8}  {"enc_%":>6}  {"lo_hi":>7}  '
          f'{"med_src_v":>10}  {"med_cit_v":>10}  {"med_inst_v":>10}')
    print('─' * 84)
    for _, r in summary.sort_values('field_idx').iterrows():
        mc = f'{r.median_citer_v:.3f}' if pd.notna(r.median_citer_v) else '    n/a'
        mi = f'{r.median_inst_v:.3f}'  if pd.notna(r.median_inst_v)  else '    n/a'
        print(
            f'{int(r.field_idx):>4}  '
            f'{FIELD_NAMES.get(int(r.field_idx), "?"):>12}  '
            f'{int(r.n_hcw):>8,}  '
            f'{int(r.n_enclave):>8,}  '
            f'{r.enc_rate:>6.1%}  '
            f'{int(r.n_lo_hi):>7,}  '
            f'{r.median_src_v:>10.3f}  '
            f'{mc:>10}  '
            f'{mi:>10}'
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Rebuild stale output without prompting')
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()

    fw_path = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(paths.working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')
    rk_paths = [
        str(paths.working / f'rankings_{fid}_{args.window}_{args.label}.parquet')
        for fid in settings.all_fields
        if (paths.working / f'rankings_{fid}_{args.window}_{args.label}.parquet').exists()
    ]
    inputs = [fw_path, cr_path] + rk_paths

    out_path = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    if not guard.ensure_fresh(out_path, *inputs, script=__file__, auto_yes=args.yes,
                              label='enclave_hcw'):
        return

    t0 = time.time()
    df = build_enclave_hcw(args.window, args.label, paths, settings)
    print_summary(df)
    df.to_parquet(out_path, index=False)
    guard.record_build(out_path, *inputs, script=__file__, build_seconds=time.time() - t0)
    print(f'\nSaved: {out_path}  ({len(df):,} HCW rows)  [{time.time()-t0:.0f}s total]')


if __name__ == '__main__':
    main()
