"""
build_field_candidacy.py — Stage 2: aggregate flat_works to per-field unit candidacy.

For each (field, source) and (field, institution), sum the field-weighted work
count over the census window. These counts drive the τ retention threshold in
Stage 3 (edge list construction).

Reads  : WORKING/flat_works_{ymin}_{ymax}.parquet
Writes : WORKING/field_source_cands_{window}.parquet
         WORKING/field_inst_cands_{window}.parquet

Schemas:
  field_source_cands: field_idx BIGINT, source_idx BIGINT, weighted_works DOUBLE
  field_inst_cands:   field_idx BIGINT, institution_idx BIGINT, weighted_works DOUBLE
"""

import sys
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings, load_runs


def build_field_candidacy(db: duckdb.DuckDBPyConnection,
                          fw_path: str,
                          window: str,
                          out_src: str,
                          out_inst: str) -> tuple[int, int]:
    """
    Aggregate flat_works to per-field source and institution candidacy parquets.

    Parameters
    ----------
    fw_path  : path to flat_works parquet (glob ok)
    window   : '{year_start}_{year_end}', e.g. '2020_2024'
    out_src  : output path for source candidacy parquet
    out_inst : output path for institution candidacy parquet

    Returns
    -------
    (n_source_rows, n_inst_rows)
    """
    tc0, tc1 = (int(x) for x in window.split('_'))

    # Deduplicate on work before summing: flat_works has one row per
    # (work × institution × field), but field_weight is per-work not per-institution.
    # Without DISTINCT, a work with N institutions contributes N × field_weight.
    db.execute(f"""
        COPY (
            SELECT field_idx, source_idx, SUM(field_weight) AS weighted_works
            FROM (
                SELECT DISTINCT work_idx, source_idx, field_idx, field_weight
                FROM '{fw_path}'
                WHERE publication_year BETWEEN {tc0} AND {tc1}
            )
            GROUP BY field_idx, source_idx
        ) TO '{out_src}' (FORMAT PARQUET)
    """)

    db.execute(f"""
        COPY (
            SELECT field_idx, institution_idx,
                   SUM(field_weight * inst_weight) AS weighted_works
            FROM '{fw_path}'
            WHERE publication_year BETWEEN {tc0} AND {tc1}
            GROUP BY field_idx, institution_idx
        ) TO '{out_inst}' (FORMAT PARQUET)
    """)

    n_s = db.execute(f"SELECT COUNT(*) FROM '{out_src}'").fetchone()[0]
    n_u = db.execute(f"SELECT COUNT(*) FROM '{out_inst}'").fetchone()[0]
    return n_s, n_u


def main():
    import time
    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    window   = runs[0].window
    fw       = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    out_s    = str(paths.working / f'field_source_cands_{window}.parquet')
    out_u    = str(paths.working / f'field_inst_cands_{window}.parquet')

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        print(f"Building field candidacy for window {window} ...")
        t0 = time.time()
        n_s, n_u = build_field_candidacy(db, fw, window, out_s, out_u)
        elapsed = time.time() - t0

        print(f"  Source rows   : {n_s:>10,}  →  {out_s}")
        print(f"  Institution rows: {n_u:>8,}  →  {out_u}")
        print(f"  Elapsed: {elapsed:.1f}s")


if __name__ == '__main__':
    main()
    print("FINISHED!")
