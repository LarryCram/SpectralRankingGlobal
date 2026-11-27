"""
build_work_scores.py — R2: per-work publication v and citer ⟨v⟩ aggregates.

Per corpus work:
  pub_v      field-weighted mean of work_v over the work's OA fields, where
             work_v = (v_S + mean v_I)/2 (pipeline/build_work_v.py) and the field
             weight is the sum of the work's subfield weights in that field.
  citer v    v of a citing work = MAX source_v across the fields in which its source
             is retained (the enclave convention, REFERENCE.md "HCW analysis").
             Citing works with no retained source have no v; they are counted
             (n_cit_raw) but excluded from the sums.
  self-cite  a citation is a self-citation when the citing and cited works share at
             least one author_idx (Stanford definition: any author of the cited paper).

Aggregates (per cited work):
  n_cit_raw                    all corpus citations
  n_all,  sv_all               citations with a citer v; Σ citer v
  n_ns,   sv_ns                the same, self-citations excluded
  n25_all, sv25_all, n25_ns, sv25_ns   citing works published in CITE_YEAR only

The self-citation join runs in N_BUCKETS buckets of cited_idx to bound memory.

Inputs:  flat_works, corpus_references, work_v_{window}_{label}, researchers/author_works
Outputs: researchers/work_scores_{ymin}_{ymax}_{label}.parquet
         researchers/work_subfields_{ymin}_{ymax}.parquet  (work_idx, subfield_idx, field_idx, w)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from researchers.common import (CITE_YEAR, N_BUCKETS, add_path_args, connect,
                                resolve_paths)
from util import guard


# ── pure SQL steps (operate on named tables; unit-tested on synthetic data) ──

def build_citer_v(con, work_v_tbl: str) -> None:
    """_cv(work_idx, v): MAX source_v across fields."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _cv AS
        SELECT work_idx, MAX(source_v) AS v
        FROM {work_v_tbl}
        GROUP BY work_idx
    """)


def build_pub_v(con, work_v_tbl: str, wsf_tbl: str) -> None:
    """_pv(work_idx, pub_v, pub_w): field-weighted mean of work_v."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _wf AS
        SELECT work_idx, field_idx, SUM(w) AS w
        FROM {wsf_tbl}
        GROUP BY work_idx, field_idx
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _pv AS
        SELECT v.work_idx,
               SUM(f.w * v.work_v) / SUM(f.w) AS pub_v,
               SUM(f.w)                      AS pub_w
        FROM {work_v_tbl} v
        JOIN _wf f ON f.work_idx = v.work_idx AND f.field_idx = v.field_idx
        WHERE f.w > 0
        GROUP BY v.work_idx
    """)


def aggregate_citations(con, r_tbl: str, awp_tbl: str, cite_year: int = CITE_YEAR,
                        out_tbl: str = '_agg') -> None:
    """
    r_tbl  : citer_idx, cited_idx, v (nullable), citer_year
    awp_tbl: work_idx, author_idx
    Appends per-cited-work aggregates to out_tbl (created if absent).
    """
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _x AS
        SELECT r.citer_idx, r.cited_idx, a.author_idx
        FROM {r_tbl} r
        JOIN {awp_tbl} a ON a.work_idx = r.cited_idx
        WHERE r.v IS NOT NULL
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _self AS
        SELECT DISTINCT x.citer_idx, x.cited_idx
        FROM _x x
        JOIN {awp_tbl} a ON a.work_idx = x.citer_idx AND a.author_idx = x.author_idx
    """)
    con.execute('DROP TABLE _x')
    con.execute(f"""
        CREATE TEMP TABLE IF NOT EXISTS {out_tbl} (
            work_idx BIGINT, n_cit_raw BIGINT,
            n_all BIGINT, sv_all DOUBLE, n_ns BIGINT, sv_ns DOUBLE,
            n25_all BIGINT, sv25_all DOUBLE, n25_ns BIGINT, sv25_ns DOUBLE)
    """)
    con.execute(f"""
        INSERT INTO {out_tbl}
        SELECT r.cited_idx AS work_idx,
               COUNT(*)                                                     AS n_cit_raw,
               COUNT(r.v)                                                   AS n_all,
               COALESCE(SUM(r.v), 0)                                        AS sv_all,
               COUNT(r.v) FILTER (WHERE s.citer_idx IS NULL)                AS n_ns,
               COALESCE(SUM(r.v) FILTER (WHERE s.citer_idx IS NULL), 0)     AS sv_ns,
               COUNT(r.v) FILTER (WHERE r.citer_year = {cite_year})         AS n25_all,
               COALESCE(SUM(r.v) FILTER (WHERE r.citer_year = {cite_year}), 0) AS sv25_all,
               COUNT(r.v) FILTER (WHERE r.citer_year = {cite_year} AND s.citer_idx IS NULL) AS n25_ns,
               COALESCE(SUM(r.v) FILTER (WHERE r.citer_year = {cite_year} AND s.citer_idx IS NULL), 0) AS sv25_ns
        FROM {r_tbl} r
        LEFT JOIN _self s ON s.citer_idx = r.citer_idx AND s.cited_idx = r.cited_idx
        GROUP BY r.cited_idx
    """)
    con.execute('DROP TABLE _self')


# ── driver ────────────────────────────────────────────────────────────────────

def build(p, con, n_buckets: int = N_BUCKETS) -> None:
    t0 = time.time()

    print('Work × subfield weights from flat_works ...')
    con.execute(f"""
        CREATE TEMP TABLE _wsf AS
        SELECT DISTINCT work_idx, subfield_idx, field_idx, field_weight AS w
        FROM parquet_scan('{p.flat_works}')
        WHERE field_idx BETWEEN 11 AND 36
    """)
    con.execute(f"COPY _wsf TO '{p.work_subfields}' (FORMAT PARQUET)")
    con.execute(f"""
        CREATE TEMP TABLE _wy AS
        SELECT work_idx, ANY_VALUE(publication_year) AS publication_year
        FROM parquet_scan('{p.flat_works}')
        GROUP BY work_idx
    """)
    print(f'  [{time.time()-t0:.0f}s]')

    con.execute(f"CREATE TEMP TABLE _wv AS SELECT work_idx, field_idx, source_v, work_v "
                f"FROM parquet_scan('{p.work_v}')")
    build_citer_v(con, '_wv')
    build_pub_v(con, '_wv', '_wsf')
    con.execute('DROP TABLE _wv')
    con.execute('DROP TABLE _wsf')
    con.execute('DROP TABLE _wf')
    print(f"  citer v: {con.execute('SELECT COUNT(*) FROM _cv').fetchone()[0]:,} works; "
          f"pub_v: {con.execute('SELECT COUNT(*) FROM _pv').fetchone()[0]:,} works  [{time.time()-t0:.0f}s]")

    print('Loading author×work pairs ...')
    con.execute(f"CREATE TEMP TABLE _awp AS SELECT work_idx, author_idx "
                f"FROM parquet_scan('{p.author_works}')")
    print(f"  {con.execute('SELECT COUNT(*) FROM _awp').fetchone()[0]:,} rows  [{time.time()-t0:.0f}s]")

    for b in range(n_buckets):
        tb = time.time()
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE _rb AS
            SELECT r.citer_idx, r.cited_idx, cv.v, wy.publication_year AS citer_year
            FROM parquet_scan('{p.corpus_refs}') r
            LEFT JOIN _cv cv ON cv.work_idx = r.citer_idx
            LEFT JOIN _wy wy ON wy.work_idx = r.citer_idx
            WHERE r.cited_idx % {n_buckets} = {b}
        """)
        aggregate_citations(con, '_rb', '_awp')
        n = con.execute('SELECT COUNT(*) FROM _rb').fetchone()[0]
        print(f'  bucket {b+1}/{n_buckets}: {n:,} refs  [{time.time()-tb:.0f}s]', flush=True)
    con.execute('DROP TABLE _rb')
    con.execute('DROP TABLE _awp')

    print('Writing work_scores ...')
    con.execute(f"""
        COPY (
            SELECT wy.work_idx, wy.publication_year, pv.pub_v, pv.pub_w,
                   COALESCE(g.n_cit_raw, 0) AS n_cit_raw,
                   COALESCE(g.n_all, 0) AS n_all, COALESCE(g.sv_all, 0) AS sv_all,
                   COALESCE(g.n_ns, 0)  AS n_ns,  COALESCE(g.sv_ns, 0)  AS sv_ns,
                   COALESCE(g.n25_all, 0) AS n25_all, COALESCE(g.sv25_all, 0) AS sv25_all,
                   COALESCE(g.n25_ns, 0)  AS n25_ns,  COALESCE(g.sv25_ns, 0)  AS sv25_ns
            FROM _wy wy
            LEFT JOIN _pv pv USING (work_idx)
            LEFT JOIN _agg g USING (work_idx)
        ) TO '{p.work_scores}' (FORMAT PARQUET)
    """)
    s = con.execute(f"""
        SELECT COUNT(*), COUNT(pub_v), SUM(n_cit_raw), SUM(n_all), SUM(n_ns),
               SUM(pub_v * pub_w) / SUM(pub_w)
        FROM parquet_scan('{p.work_scores}')
    """).fetchone()
    print(f'  works {s[0]:,}; with pub_v {s[1]:,}; citations raw {s[2]:,}, '
          f'with v {s[3]:,}, non-self {s[4]:,}; weighted mean pub_v {s[5]:.3f}')
    print(f'Total [{time.time()-t0:.0f}s]')


def main() -> None:
    ap = argparse.ArgumentParser()
    add_path_args(ap)
    ap.add_argument('--buckets', type=int, default=N_BUCKETS)
    args = ap.parse_args()
    p = resolve_paths(args)
    inputs = [p.flat_works, p.corpus_refs, p.work_v, p.author_works]
    missing = [str(x) for x in inputs if not Path(x).exists()]
    if missing:
        sys.exit(f'missing inputs: {missing}')
    if not guard.ensure_fresh(p.work_scores, *inputs, script=__file__,
                              auto_yes=args.yes, label='work_scores'):
        return
    t0 = time.time()
    build(p, connect(), n_buckets=args.buckets)
    for out in (p.work_scores, p.work_subfields):
        guard.record_build(out, *inputs, script=__file__, build_seconds=time.time() - t0)


if __name__ == '__main__':
    main()
