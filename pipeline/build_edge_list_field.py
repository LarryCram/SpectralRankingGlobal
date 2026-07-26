"""
build_edge_list_field.py — Stage 3: build citation edge list for one field.

Reads  : WORKING/flat_works_{ymin}_{ymax}.parquet
         WORKING/corpus_references_{ymin}_{ymax}.parquet  (columns: citer_idx, cited_idx)
         WORKING/field_source_cands_{window}.parquet
         WORKING/field_inst_cands_{window}.parquet
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
from util import load_config, load_settings, load_runs, Run

SX_IDX = 1   # sentinel source_idx for ε=1 runs
IX_IDX = 1   # sentinel institution_idx for ε=1 runs


def _add_epsilon_edges(db: duckdb.DuckDBPyConnection,
                       corpus_refs_path: str) -> None:
    """
    Insert cross-field sentinel edges into _edges_out (must already exist).

    Requires _fw (retained works: work_idx, field_weight) and _fi (work×inst
    rows with inst_weight etc.) and _Ri (within-field R_i per citer work) to
    exist as temp tables in `db`.

    type1 — retained citer  →  sentinel cited:
        citer is in _fw; cited is in corpus_refs but NOT in _fw.
        edge_field_weight = citer_fw / 2   (cited side has zero field weight)
        R_i = within-field R_i of citer (reuses existing _Ri)

    type2 — sentinel citer  →  retained cited:
        cited is in _fw; citer is in corpus_refs but NOT in _fw.
        edge_field_weight = cited_fw / 2   (citer side has zero field weight)
        R_i = SUM(cited_fw / 2) over all retained works cited by this citer
    """
    # type1: retained citer → sentinel cited
    db.execute(f"""
        INSERT INTO _edges_out
        WITH xf_cited AS (
            SELECT ref.citer_idx, ref.cited_idx
            FROM '{corpus_refs_path}' ref
            WHERE ref.citer_idx IN (SELECT work_idx FROM _fw)
              AND ref.cited_idx  NOT IN (SELECT work_idx FROM _fw)
        )
        SELECT
            ci.work_idx                AS citer_work_idx,
            ci.source_idx              AS citer_source_idx,
            ci.institution_idx         AS citer_inst_idx,
            ci.inst_weight,
            ci.direct_inst_weight,
            x.cited_idx                AS cited_work_idx,
            {SX_IDX}::BIGINT           AS cited_source_idx,
            {IX_IDX}::BIGINT           AS cited_inst_idx,
            1.0                        AS cited_inst_weight,
            1.0                        AS direct_cited_inst_weight,
            ci.field_weight / 2.0      AS edge_field_weight,
            ri.R_i
        FROM xf_cited x
        JOIN _fi ci ON x.citer_idx = ci.work_idx
        JOIN _Ri ri ON x.citer_idx = ri.citer_work_idx
    """)

    # type2: sentinel citer → retained cited
    db.execute(f"""
        INSERT INTO _edges_out
        WITH xf_citer AS (
            SELECT ref.citer_idx, ref.cited_idx
            FROM '{corpus_refs_path}' ref
            WHERE ref.cited_idx  IN (SELECT work_idx FROM _fw)
              AND ref.citer_idx NOT IN (SELECT work_idx FROM _fw)
        ),
        ri_t2 AS (
            SELECT x.citer_idx AS work_idx,
                   SUM(fw.field_weight / 2.0) AS R_i
            FROM xf_citer x
            JOIN _fw fw ON x.cited_idx = fw.work_idx
            GROUP BY x.citer_idx
        )
        SELECT
            x.citer_idx                AS citer_work_idx,
            {SX_IDX}::BIGINT           AS citer_source_idx,
            {IX_IDX}::BIGINT           AS citer_inst_idx,
            1.0                        AS inst_weight,
            1.0                        AS direct_inst_weight,
            cd.work_idx                AS cited_work_idx,
            cd.source_idx              AS cited_source_idx,
            cd.institution_idx         AS cited_inst_idx,
            cd.inst_weight             AS cited_inst_weight,
            cd.direct_inst_weight      AS direct_cited_inst_weight,
            cd.field_weight / 2.0      AS edge_field_weight,
            ri.R_i
        FROM xf_citer x
        JOIN _fi cd ON x.cited_idx = cd.work_idx
        JOIN ri_t2 ri ON x.citer_idx = ri.work_idx
    """)


def build_edge_list(db: duckdb.DuckDBPyConnection,
                    fw_path: str,
                    corpus_refs_path: str,
                    sc_path: str,
                    ic_path: str,
                    run: Run,
                    out_path: str,
                    bloc_codes: tuple = (),
                    member_ids: tuple = (),
                    filter_col_override: str = None) -> int:
    """
    Build field-specific citation edge list parquet for one Run.

    Retention filter: source/institution must have weighted_works >= tau * window_years
    in the candidacy tables (Stage 2 output).

    bloc_codes: when non-empty, apply all-in country filter — a work is included only
    if every institution affiliated with it (in this field+window) has a country_code
    in bloc_codes.  Pass tuple(settings.blocs[run.bloc]) when run.bloc != ''.

    member_ids / filter_col_override: for grouping schemes decoupled from Run's own
    field_idx-range dispatch (e.g. AREA5 grouping several OA field_idx into one area,
    FOR-division grouping several subfield_idx into one division) — when member_ids is
    non-empty, flat_works is filtered by `filter_col IN member_ids` instead of
    `filter_col = run.field_idx`; filter_col_override picks the column ('field_idx' or
    'subfield_idx') explicitly instead of inferring it from run.is_leiden/is_subfield.
    run.field_idx itself is still used as this call's own scratch-candidacy identity
    (sc_path/ic_path are expected to already be scoped to that single group code).

    Returns row count of the output edge list.
    """
    tc0, tc1 = (int(x) for x in run.window.split('_'))
    field_idx = run.field_idx
    tau_s_abs = run.tau_s_abs()
    tau_u_abs = run.tau_u_abs()

    # Dispatch filter column based on run type:
    #   leiden   (field_idx 1–5)    → filter by leiden_idx
    #   subfield (field_idx ≥ 1000) → filter by subfield_idx; no aggregation needed
    #   field    (field_idx 11–36)  → filter by field_idx; aggregate subfields
    if filter_col_override is not None:
        filter_col = filter_col_override
    elif run.is_leiden:
        filter_col = 'leiden_idx'
    elif run.is_subfield:
        filter_col = 'subfield_idx'
    else:
        filter_col = 'field_idx'

    if member_ids:
        match_clause = f"{filter_col} IN ({', '.join(str(m) for m in member_ids)})"
    else:
        match_clause = f"{filter_col} = {field_idx}"

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

    # ── 2. Flat works for this field/leiden/subfield + window ─────────────────
    # All-in bloc filter: exclude any work whose institution set is not fully
    # within bloc_codes.  Checked across ALL institutions in this group+window
    # (not just retained units) so even below-tau collaborators trigger exclusion.
    if bloc_codes:
        codes_sql = ', '.join(f"'{c}'" for c in bloc_codes)
        db.execute(f"""
            CREATE OR REPLACE TEMP TABLE _excl_works AS
            SELECT DISTINCT work_idx FROM '{fw_path}'
            WHERE {match_clause}
              AND publication_year BETWEEN {tc0} AND {tc1}
              AND country_code NOT IN ({codes_sql})
        """)
        n_excl = db.execute("SELECT COUNT(*) FROM _excl_works").fetchone()[0]
        print(f"  Bloc filter: {n_excl:,} works excluded (not all-in bloc)")
        bloc_clause = "AND work_idx NOT IN (SELECT work_idx FROM _excl_works)"
    else:
        bloc_clause = ""

    # For field and leiden modes, flat_works is at subfield granularity so we
    # GROUP BY (work, source, institution) to aggregate subfield weights.
    # For subfield mode, rows are already at the right granularity.
    if run.is_subfield:
        db.execute(f"""
            CREATE OR REPLACE TEMP TABLE _fi AS
            SELECT work_idx, source_idx, institution_idx,
                   inst_weight, direct_inst_weight, field_weight
            FROM '{fw_path}'
            WHERE {match_clause}
              AND publication_year BETWEEN {tc0} AND {tc1}
              AND source_idx      IN (SELECT source_idx      FROM _cands_s)
              AND institution_idx IN (SELECT institution_idx FROM _cands_u)
              {bloc_clause}
        """)
    else:
        db.execute(f"""
            CREATE OR REPLACE TEMP TABLE _fi AS
            SELECT work_idx, source_idx, institution_idx,
                   ANY_VALUE(inst_weight)        AS inst_weight,
                   ANY_VALUE(direct_inst_weight) AS direct_inst_weight,
                   SUM(field_weight)             AS field_weight
            FROM '{fw_path}'
            WHERE {match_clause}
              AND publication_year BETWEEN {tc0} AND {tc1}
              AND source_idx      IN (SELECT source_idx      FROM _cands_s)
              AND institution_idx IN (SELECT institution_idx FROM _cands_u)
              {bloc_clause}
            GROUP BY work_idx, source_idx, institution_idx
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
        JOIN '{corpus_refs_path}' ref ON cw.work_idx = ref.citer_idx
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
        CREATE OR REPLACE TEMP TABLE _edges_out AS
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
    """)

    if run.epsilon:
        _add_epsilon_edges(db, corpus_refs_path)

    db.execute(f"COPY (SELECT * FROM _edges_out) TO '{out_path}' (FORMAT PARQUET)")

    db.execute("DROP TABLE IF EXISTS _cands_s")
    db.execute("DROP TABLE IF EXISTS _cands_u")
    db.execute("DROP TABLE IF EXISTS _excl_works")
    db.execute("DROP TABLE IF EXISTS _fi")
    db.execute("DROP TABLE IF EXISTS _fw")
    db.execute("DROP TABLE IF EXISTS _pairs")
    db.execute("DROP TABLE IF EXISTS _Ri")
    db.execute("DROP TABLE IF EXISTS _edges_out")

    return db.execute(f"SELECT COUNT(*) FROM '{out_path}'").fetchone()[0]


def main():
    import time
    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    run      = runs[0]

    fw_path  = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path  = str(paths.working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')
    sc_path  = run.sc_path(str(paths.working))
    ic_path  = run.ic_path(str(paths.working))

    from dataclasses import replace
    run      = replace(run, field_idx=14)   # single-field entry point: default field 14
    out_path = run.el_path(str(paths.working))

    for p in [sc_path, ic_path, cr_path]:
        if not Path(p).exists():
            raise FileNotFoundError(
                f"{p} not found — run build_flat_works.py / build_field_candidacy.py first"
            )

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        bloc_codes = tuple(settings.blocs.get(run.bloc, ())) if run.bloc else ()
        print(f"Building edge list: field={run.field_idx}, window={run.window}, "
              f"τ_s={run.tau_s}/yr, τ_u={run.tau_u}/yr, label={run.label!r}"
              + (f", bloc={run.bloc!r} ({len(bloc_codes)} countries)" if bloc_codes else "") + " ...")
        t0 = time.time()
        n = build_edge_list(db, fw_path, cr_path, sc_path, ic_path, run, out_path,
                            bloc_codes=bloc_codes)
        elapsed = time.time() - t0

        print(f"  Edge list rows: {n:,}")
        print(f"  Elapsed: {elapsed:.1f}s")
        print(f"  → {out_path}")


if __name__ == '__main__':
    main()
    print("FINISHED!")
