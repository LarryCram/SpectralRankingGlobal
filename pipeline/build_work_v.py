"""
build_work_v.py — Stage 4c: compute work_v = (v_S + mean(v_I)) / 2 for every
(work_idx, field_idx) pair where the source is retained in that field's rankings.

Covers all 26 OA fields (field_idx 11–36).  Reads flat_works once and joins
against all 26 rankings files in a single DuckDB session.

mean_inst_v is the unweighted mean of retained-institution v-scores for the work
in the given field.  If no retained institutions, mean_inst_v is null and work_v
falls back to source_v (i.e. work_v = source_v).

Input:
  WORKING/flat_works_{ymin}_{ymax}.parquet
  WORKING/rankings_{field_idx}_{window}_{label}.parquet   (26 files, unit_type S/U)

Output:
  WORKING/work_v_{window}_{label}.parquet
    cols: work_idx (BIGINT), field_idx (BIGINT),
          source_v (DOUBLE), mean_inst_v (DOUBLE), work_v (DOUBLE)

Usage:
  .venv/bin/python pipeline/build_work_v.py
  .venv/bin/python pipeline/build_work_v.py --window 2020_2024 --label baseline
"""

import sys
import time
import argparse
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings

FIELD_IDS = list(range(11, 37))


def build_work_v(
    fw_path: str,
    rankings_paths: list[str],
    out_path: Path,
) -> None:
    t0 = time.time()
    con = duckdb.connect()

    # ── load all field rankings into one temp table ───────────────────────────
    print('Loading rankings...')
    rlist = "['" + "', '".join(rankings_paths) + "']"
    con.execute(f"""
        CREATE TEMP TABLE _ranks AS
        SELECT field_idx, unit_idx, unit_type, v
        FROM parquet_scan({rlist})
        WHERE unit_type IN ('S', 'U')
    """)
    n = con.execute('SELECT COUNT(*) FROM _ranks').fetchone()[0]
    print(f'  {n:,} rows  [{time.time()-t0:.1f}s]')

    # ── one flat_works pass: distinct (work×field×source×institution) ─────────
    print('Extracting distinct work×institution rows from flat_works...')
    t1 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _wi AS
        SELECT DISTINCT work_idx, field_idx, source_idx, institution_idx
        FROM parquet_scan('{fw_path}')
        WHERE field_idx BETWEEN 11 AND 36
    """)
    n = con.execute('SELECT COUNT(*) FROM _wi').fetchone()[0]
    print(f'  {n:,} distinct (work×institution) rows  [{time.time()-t1:.1f}s]')

    # ── join source + institution v; aggregate to (work×field) ───────────────
    print('Joining rankings and computing work_v...')
    t2 = time.time()
    con.execute("""
        CREATE TEMP TABLE _agg AS
        SELECT
            wi.work_idx,
            wi.field_idx,
            sv.v         AS source_v,
            AVG(iv.v)    AS mean_inst_v
        FROM _wi wi
        JOIN   (SELECT field_idx, unit_idx, v FROM _ranks WHERE unit_type = 'S') sv
          ON  wi.field_idx = sv.field_idx AND wi.source_idx = sv.unit_idx
        LEFT JOIN (SELECT field_idx, unit_idx, v FROM _ranks WHERE unit_type = 'U') iv
          ON  wi.field_idx = iv.field_idx AND wi.institution_idx = iv.unit_idx
        GROUP BY wi.work_idx, wi.field_idx, sv.v
    """)
    n = con.execute('SELECT COUNT(*) FROM _agg').fetchone()[0]
    print(f'  {n:,} (work×field) rows  [{time.time()-t2:.1f}s]')

    # ── write output ──────────────────────────────────────────────────────────
    print(f'Writing {out_path.name}...')
    t3 = time.time()
    con.execute(f"""
        COPY (
            SELECT
                work_idx,
                field_idx,
                source_v,
                mean_inst_v,
                (source_v + COALESCE(mean_inst_v, source_v)) / 2 AS work_v
            FROM _agg
            ORDER BY field_idx, work_idx
        ) TO '{out_path}' (FORMAT PARQUET)
    """)
    con.close()
    print(f'  saved  [{time.time()-t3:.1f}s]')
    print(f'Total [{time.time()-t0:.1f}s]')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()

    fw_path = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    if not Path(fw_path).exists():
        print(f'ERROR: {fw_path} not found')
        sys.exit(1)

    rankings_paths: list[str] = []
    missing: list[int] = []
    for fid in FIELD_IDS:
        p = paths.working / f'rankings_{fid}_{args.window}_{args.label}.parquet'
        if p.exists():
            rankings_paths.append(str(p))
        else:
            missing.append(fid)
    if missing:
        print(f'WARNING: missing rankings for fields {missing}')
    if not rankings_paths:
        print('ERROR: no rankings files found')
        sys.exit(1)

    out_path = paths.working / f'work_v_{args.window}_{args.label}.parquet'
    print(f'Building work_v — {len(rankings_paths)} fields → {out_path.name}')
    build_work_v(fw_path, rankings_paths, out_path)


if __name__ == '__main__':
    main()
