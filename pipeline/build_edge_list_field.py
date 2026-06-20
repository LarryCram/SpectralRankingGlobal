"""
build_edge_list_field.py — Stage 3: build citation edge list for one field.

Reads  : WORKING/flat_works_{ymin}_{ymax}.parquet
         WORKING/field_source_cands_{window}.parquet
         WORKING/field_inst_cands_{window}.parquet
         OPENALEX/parquet/references/*.parquet  (columns: citer_idx, cited_idx)
Writes : WORKING/el_{field_idx}_{window}_tauS{tau_s}_tauU{tau_u}.parquet

Schema of output edge list (one row per citer_inst × cited_inst):
  citer_work_idx          BIGINT
  citer_source_idx        BIGINT
  citer_inst_idx          BIGINT
  cited_work_idx          BIGINT
  cited_source_idx        BIGINT
  cited_inst_idx          BIGINT
  inst_weight             DOUBLE  -- citer author-fractional inst weight
  direct_inst_weight      DOUBLE  -- citer institution-fractional inst weight
  cited_inst_weight       DOUBLE  -- cited author-fractional inst weight
  direct_cited_inst_weight DOUBLE  -- cited institution-fractional inst weight
  edge_field_weight       DOUBLE  -- (citer_fw + cited_fw) / 2
  R_i                     DOUBLE  -- SUM(edge_field_weight) over all cited works for citer_work
"""

import sys
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, Run

YEAR_MIN = 2016
YEAR_MAX = 2025


def build_edge_list(db: duckdb.DuckDBPyConnection,
                    fw_path: str,
                    refs_path: str,
                    sc_path: str,
                    ic_path: str,
                    run: Run,
                    out_path: str) -> int:
    """
    Build field-specific citation edge list parquet for one Run.

    Retention filter: source/institution must have weighted_works >= tau * window_years
    in the candidacy tables (Stage 2 output).

    Returns row count of the output edge list.
    """
    tc0, tc1 = (int(x) for x in run.window.split('_'))
    field_idx = run.field_idx
    tau_s_abs = run.tau_s_abs()
    tau_u_abs = run.tau_u_abs()

    # ── 1. Retained units ─────────────────────────────────────────────────────
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _cands_s AS
        SELECT source_idx
        FROM '{sc_path}'
        WHERE field_idx = {field_idx} AND weighted_works >= {tau_s_abs}
    """)
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _cands_u AS
        SELECT institution_idx
        FROM '{ic_path}'
        WHERE field_idx = {field_idx} AND weighted_works >= {tau_u_abs}
    """)

    n_s = db.execute("SELECT COUNT(*) FROM _cands_s").fetchone()[0]
    n_u = db.execute("SELECT COUNT(*) FROM _cands_u").fetchone()[0]
    print(f"  Retained: {n_s:,} sources, {n_u:,} institutions  "
          f"(τ_s={tau_s_abs:.0f}, τ_u={tau_u_abs:.0f})")

    # ── 2. Flat works for this field + window, filtered to retained units ─────
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _fi AS
        SELECT work_idx, source_idx, institution_idx,
               inst_weight, direct_inst_weight, field_weight
        FROM '{fw_path}'
        WHERE field_idx = {field_idx}
          AND publication_year BETWEEN {tc0} AND {tc1}
          AND source_idx      IN (SELECT source_idx      FROM _cands_s)
          AND institution_idx IN (SELECT institution_idx FROM _cands_u)
    """)

    n_rows = db.execute("SELECT COUNT(*) FROM _fi").fetchone()[0]
    n_works = db.execute("SELECT COUNT(DISTINCT work_idx) FROM _fi").fetchone()[0]
    print(f"  Corpus: {n_works:,} works, {n_rows:,} (work×inst) rows in flat_works")

    # ── 3. Unique work-level (field_weight is per-work, same across insts) ────
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _fw AS
        SELECT DISTINCT work_idx, field_weight FROM _fi
    """)

    # ── 4. Work-level reference pairs (before institution expansion) ──────────
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _pairs AS
        SELECT cw.work_idx  AS citer_work_idx, cw.field_weight AS citer_fw,
               cd.work_idx  AS cited_work_idx, cd.field_weight AS cited_fw,
               (cw.field_weight + cd.field_weight) / 2.0 AS edge_field_weight
        FROM _fw cw
        JOIN '{refs_path}' ref ON cw.work_idx = ref.citer_idx
        JOIN _fw cd ON ref.cited_idx = cd.work_idx
    """)

    n_pairs = db.execute("SELECT COUNT(*) FROM _pairs").fetchone()[0]
    print(f"  Work-level citation pairs: {n_pairs:,}")

    if n_pairs == 0:
        print("  WARNING: no citation pairs found; edge list will be empty")

    # ── 5. R_i: sum of edge_field_weight over all cited works per citer ───────
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _Ri AS
        SELECT citer_work_idx, SUM(edge_field_weight) AS R_i
        FROM _pairs
        GROUP BY citer_work_idx
    """)

    # ── 6. Full edge list: expand pairs to (citer_inst × cited_inst) ─────────
    db.execute(f"""
        COPY (
            SELECT
                ci.work_idx          AS citer_work_idx,
                ci.source_idx        AS citer_source_idx,
                ci.institution_idx   AS citer_inst_idx,
                ci.inst_weight,
                ci.direct_inst_weight,
                cd.work_idx          AS cited_work_idx,
                cd.source_idx        AS cited_source_idx,
                cd.institution_idx   AS cited_inst_idx,
                cd.inst_weight       AS cited_inst_weight,
                cd.direct_inst_weight AS direct_cited_inst_weight,
                p.edge_field_weight,
                ri.R_i
            FROM _pairs p
            JOIN _fi ci ON p.citer_work_idx = ci.work_idx
            JOIN _fi cd ON p.cited_work_idx = cd.work_idx
            JOIN _Ri ri ON p.citer_work_idx = ri.citer_work_idx
        ) TO '{out_path}' (FORMAT PARQUET)
    """)

    db.execute("DROP TABLE IF EXISTS _cands_s")
    db.execute("DROP TABLE IF EXISTS _cands_u")
    db.execute("DROP TABLE IF EXISTS _fi")
    db.execute("DROP TABLE IF EXISTS _fw")
    db.execute("DROP TABLE IF EXISTS _pairs")
    db.execute("DROP TABLE IF EXISTS _Ri")

    return db.execute(f"SELECT COUNT(*) FROM '{out_path}'").fetchone()[0]


def main():
    import time
    paths = load_config()

    run = Run(
        window='2020_2024',
        field_idx=14,          # Business, Management and Accounting
        tau_s=20.0,
        tau_u=20.0,
        m=(0, 1, 1, 0),
        alpha=1.0,
        rho=0,
        label='business_baseline',
    )

    fw_path   = str(paths.working / f'flat_works_{YEAR_MIN}_{YEAR_MAX}.parquet')
    refs_path = f'{paths.openalex}/parquet/references/*.parquet'
    sc_path   = run.sc_path(str(paths.working))
    ic_path   = run.ic_path(str(paths.working))
    out_path  = run.el_path(str(paths.working))

    for p in [sc_path, ic_path]:
        if not Path(p).exists():
            raise FileNotFoundError(
                f"{p} not found — run build_field_candidacy.py first"
            )

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute("SET memory_limit = '56GB'")
        db.execute("SET preserve_insertion_order = false")

        print(f"Building edge list: field={run.field_idx}, window={run.window}, "
              f"τ_s={run.tau_s}/yr, τ_u={run.tau_u}/yr ...")
        t0 = time.time()
        n = build_edge_list(db, fw_path, refs_path, sc_path, ic_path, run, out_path)
        elapsed = time.time() - t0

        print(f"  Edge list rows: {n:,}")
        print(f"  Elapsed: {elapsed:.1f}s")
        print(f"  → {out_path}")


if __name__ == '__main__':
    main()
    print("FINISHED!")
