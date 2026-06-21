"""
build_field_candidacy.py — Stage 2: aggregate flat_works to unit candidacy.

Produces per-(group, source) and per-(group, institution) weighted work counts
over the census window.  Three grouping levels are supported:
  field_idx   (11–36)  → field_source_cands_{window}.parquet / field_inst_cands_{window}.parquet
  leiden_idx   (1– 5)  → leiden_source_cands_{window}.parquet / leiden_inst_cands_{window}.parquet
  subfield_idx (1000+) → subfield_source_cands_{window}.parquet / subfield_inst_cands_{window}.parquet

All output tables share the same schema with field_idx as the grouping column:
  field_source_cands: field_idx BIGINT, source_idx BIGINT, weighted_works DOUBLE
  field_inst_cands:   field_idx BIGINT, institution_idx BIGINT, weighted_works DOUBLE

Row granularity of flat_works is (work × institution × subfield).
field_weight is subfield_weight (sums to 1 per work across all subfields).

Source candidacy: SUM(field_weight) over DISTINCT (work, source, group, subfield) —
  the DISTINCT deduplicates across institutions (field_weight is per-work, not per-inst).
  Including subfield_idx in the DISTINCT ensures correctness when two subfields in
  the same (work, field) share the same field_weight value.

Institution candidacy: SUM(field_weight × inst_weight) per (group, institution) —
  each (work, institution, subfield) row contributes independently; no dedup needed.
"""

import sys
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings, load_runs


def build_candidacy(db: duckdb.DuckDBPyConnection,
                    fw_path: str,
                    window: str,
                    out_src: str,
                    out_inst: str,
                    group_col: str = 'field_idx') -> tuple[int, int]:
    """
    Generic candidacy builder.  group_col is 'field_idx', 'leiden_idx', or 'subfield_idx'.
    Output field_idx column holds the group values (1-5 for leiden, 1000+ for subfield).

    Returns (n_source_rows, n_inst_rows).
    """
    tc0, tc1 = (int(x) for x in window.split('_'))

    if group_col == 'subfield_idx':
        distinct_cols = 'work_idx, source_idx, subfield_idx, field_weight'
    else:
        distinct_cols = f'work_idx, source_idx, {group_col}, subfield_idx, field_weight'

    db.execute(f"""
        COPY (
            SELECT {group_col} AS field_idx, source_idx, SUM(field_weight) AS weighted_works
            FROM (
                SELECT DISTINCT {distinct_cols}
                FROM '{fw_path}'
                WHERE publication_year BETWEEN {tc0} AND {tc1}
            )
            GROUP BY {group_col}, source_idx
        ) TO '{out_src}' (FORMAT PARQUET)
    """)

    db.execute(f"""
        COPY (
            SELECT {group_col} AS field_idx, institution_idx,
                   SUM(field_weight * inst_weight) AS weighted_works
            FROM '{fw_path}'
            WHERE publication_year BETWEEN {tc0} AND {tc1}
            GROUP BY {group_col}, institution_idx
        ) TO '{out_inst}' (FORMAT PARQUET)
    """)

    n_s = db.execute(f"SELECT COUNT(*) FROM '{out_src}'").fetchone()[0]
    n_u = db.execute(f"SELECT COUNT(*) FROM '{out_inst}'").fetchone()[0]
    return n_s, n_u


def build_field_candidacy(db: duckdb.DuckDBPyConnection,
                          fw_path: str,
                          window: str,
                          out_src: str,
                          out_inst: str) -> tuple[int, int]:
    return build_candidacy(db, fw_path, window, out_src, out_inst, 'field_idx')


def build_leiden_candidacy(db: duckdb.DuckDBPyConnection,
                           fw_path: str,
                           window: str,
                           out_src: str,
                           out_inst: str) -> tuple[int, int]:
    return build_candidacy(db, fw_path, window, out_src, out_inst, 'leiden_idx')


def build_subfield_candidacy(db: duckdb.DuckDBPyConnection,
                             fw_path: str,
                             window: str,
                             out_src: str,
                             out_inst: str) -> tuple[int, int]:
    return build_candidacy(db, fw_path, window, out_src, out_inst, 'subfield_idx')


def main():
    import time
    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    window   = runs[0].window
    fw       = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        for label, build_fn, out_s_name, out_u_name in [
            ('field',    build_field_candidacy,    f'field_source_cands_{window}.parquet',
                                                    f'field_inst_cands_{window}.parquet'),
            ('leiden',   build_leiden_candidacy,   f'leiden_source_cands_{window}.parquet',
                                                    f'leiden_inst_cands_{window}.parquet'),
            ('subfield', build_subfield_candidacy, f'subfield_source_cands_{window}.parquet',
                                                    f'subfield_inst_cands_{window}.parquet'),
        ]:
            out_s = str(paths.working / out_s_name)
            out_u = str(paths.working / out_u_name)
            print(f"Building {label} candidacy for window {window} ...")
            t0 = time.time()
            n_s, n_u = build_fn(db, fw, window, out_s, out_u)
            elapsed = time.time() - t0
            print(f"  Source rows   : {n_s:>10,}  →  {out_s}")
            print(f"  Inst rows     : {n_u:>10,}  →  {out_u}")
            print(f"  Elapsed: {elapsed:.1f}s")


if __name__ == '__main__':
    main()
    print("FINISHED!")
