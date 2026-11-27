"""
build_author_scores.py — R3: researcher v-scores for every eligible OpenAlex author.

Eligible: >= MIN_WORKS corpus works of type article/review/conference-paper
(author_volume.eligible). Scores are means over those works only.

Per-work inputs (work_scores): pub_v; citation aggregates n_k, sv_k for
k in {all, ns, 25_all, 25_ns}; a work's citer ⟨v⟩_k = sv_k / n_k when n_k > 0.

Author measures (counting: full = weight 1 per work; frac = weight 1/authors_count):
  pubv_full, pubv_frac                 mean pub_v over works that have one
  cv{_full,_frac}_{all,ns}             career: mean ⟨v⟩ over cited works, with / without self-cites
  cv{_full,_frac}_{25all,25ns}         single-year: ⟨v⟩ from citing works published in CITE_YEAR
Uncited works are excluded from the ⟨v⟩ means; n_cited_* gives the count.
A measure is ranked (rank / top_field / top_sub columns) only when its contributing
work count is >= MIN_WORKS.

Also: total citations (raw, with v, non-self, CITE_YEAR), self-citation share,
main OA field and subfield (largest Σ subfield weight over the author's works),
and OpenAlex author metadata (display_name, orcid, h_index, works_count,
last known institution and country).

Output: WORKING/researchers/author_v_{ymin}_{ymax}_{label}.parquet
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from researchers.common import MIN_WORKS, add_path_args, connect, resolve_paths
from util import guard

# measure -> column holding its contributing-work count
MEASURES = {
    'pubv_full': 'n_pub', 'pubv_frac': 'n_pub',
    'cv_full_all': 'n_cited_all', 'cv_frac_all': 'n_cited_all',
    'cv_full_ns': 'n_cited_ns', 'cv_frac_ns': 'n_cited_ns',
    'cv_full_25all': 'n_cited_25all', 'cv_frac_25all': 'n_cited_25all',
    'cv_full_25ns': 'n_cited_25ns', 'cv_frac_25ns': 'n_cited_25ns',
}
_CIT = {'all': ('n_all', 'sv_all'), 'ns': ('n_ns', 'sv_ns'),
        '25all': ('n25_all', 'sv25_all'), '25ns': ('n25_ns', 'sv25_ns')}


def aggregate_authors(con, aw_tbl: str, ws_tbl: str, out_tbl: str = '_as') -> None:
    """
    aw_tbl: author_idx, work_idx, authors_count   (eligible authors, Stanford types)
    ws_tbl: work_idx, pub_v, n_cit_raw, n_all, sv_all, n_ns, sv_ns, n25_all, sv25_all, n25_ns, sv25_ns
    """
    cols = []
    for k, (n, sv) in _CIT.items():
        c = f'{n} > 0'
        cols += [
            f'COUNT(*) FILTER (WHERE {c}) AS n_cited_{k}',
            f'AVG({sv} / {n}) FILTER (WHERE {c}) AS cv_full_{k}',
            f'SUM(a.f * {sv} / {n}) FILTER (WHERE {c}) / SUM(a.f) FILTER (WHERE {c}) AS cv_frac_{k}',
        ]
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {out_tbl} AS
        SELECT a.author_idx,
               COUNT(*)                                                  AS n_works,
               COUNT(w.pub_v)                                            AS n_pub,
               AVG(w.pub_v)                                              AS pubv_full,
               SUM(a.f * w.pub_v) / SUM(a.f) FILTER (WHERE w.pub_v IS NOT NULL)        AS pubv_frac,
               AVG(a.authors_count)                                      AS mean_authors,
               {', '.join(cols)},
               SUM(w.n_cit_raw) AS cites_raw, SUM(w.n_all) AS cites_all, SUM(w.n_ns) AS cites_ns,
               SUM(w.n25_all) AS cites_25all, SUM(w.n25_ns) AS cites_25ns,
               CASE WHEN SUM(w.n_all) > 0
                    THEN 1 - SUM(w.n_ns)::DOUBLE / SUM(w.n_all) END     AS self_share
        FROM (SELECT author_idx, work_idx, authors_count,
                     1.0 / GREATEST(COALESCE(authors_count, 1), 1) AS f
              FROM {aw_tbl}) a
        JOIN {ws_tbl} w USING (work_idx)
        GROUP BY a.author_idx
    """)


def rank_measures(min_works: int = MIN_WORKS) -> str:
    """SELECT-list of rank (global) and top-fraction (within main field / subfield) per measure.

    rank 1 = highest. The eligibility flag is part of each partition so that authors below
    the contributing-work threshold neither receive nor occupy ranks.
    """
    exprs = []
    for m, n in MEASURES.items():
        ok = f'({m} IS NOT NULL AND {n} >= {min_works})'
        exprs.append(f'CASE WHEN {ok} THEN RANK() OVER (PARTITION BY {ok} ORDER BY {m} DESC) END AS rank_{m}')
        for tag, grp in (('field', 'main_field'), ('sub', 'main_subfield')):
            w = f'PARTITION BY {grp}, {ok}'
            exprs.append(f'CASE WHEN {ok} THEN RANK() OVER ({w} ORDER BY {m} DESC)::DOUBLE '
                         f'/ COUNT(*) OVER ({w}) END AS top_{tag}_{m}')
    return ',\n'.join(exprs)


def build(p, con) -> None:
    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _aw AS
        SELECT aw.author_idx, aw.work_idx, aw.authors_count
        FROM parquet_scan('{p.author_works}') aw
        JOIN (SELECT author_idx FROM parquet_scan('{p.author_volume}') WHERE eligible) e
          USING (author_idx)
        WHERE aw.stanford_type
    """)
    print(f"  {con.execute('SELECT COUNT(*) FROM _aw').fetchone()[0]:,} eligible author×work rows  [{time.time()-t0:.0f}s]")

    con.execute(f"CREATE TEMP TABLE _ws AS SELECT * FROM parquet_scan('{p.work_scores}')")
    aggregate_authors(con, '_aw', '_ws')
    con.execute('DROP TABLE _ws')
    print(f'  aggregated  [{time.time()-t0:.0f}s]')

    # main field / subfield by Σ subfield weight
    con.execute(f"""
        CREATE TEMP TABLE _asf AS
        SELECT a.author_idx, s.subfield_idx, s.field_idx, SUM(s.w) AS w
        FROM _aw a JOIN parquet_scan('{p.work_subfields}') s USING (work_idx)
        GROUP BY a.author_idx, s.subfield_idx, s.field_idx
    """)
    con.execute("""
        CREATE TEMP TABLE _main AS
        SELECT f.author_idx, f.main_field, f.main_field_frac, s.main_subfield, s.main_subfield_frac
        FROM (SELECT author_idx, arg_max(field_idx, w) AS main_field, MAX(w) / SUM(w) AS main_field_frac
              FROM (SELECT author_idx, field_idx, SUM(w) AS w FROM _asf GROUP BY 1, 2)
              GROUP BY author_idx) f
        JOIN (SELECT author_idx, arg_max(subfield_idx, w) AS main_subfield, MAX(w) / SUM(w) AS main_subfield_frac
              FROM _asf GROUP BY author_idx) s USING (author_idx)
    """)
    con.execute('DROP TABLE _asf')
    con.execute('DROP TABLE _aw')
    print(f'  main field/subfield  [{time.time()-t0:.0f}s]')

    con.execute(f"""
        CREATE TEMP TABLE _meta AS
        SELECT a.author_idx, a.display_name, a.orcid, a.h_index AS oa_h_index,
               a.works_count AS oa_works_count, a.cited_by_count AS oa_cited_by_count,
               a.last_known_institutions[1].display_name AS last_inst,
               a.last_known_institutions[1].country_code AS last_country
        FROM parquet_scan('{p.authors_glob}') a
        SEMI JOIN _as USING (author_idx)
    """)
    con.execute(f"""
        CREATE TEMP TABLE _full AS
        SELECT s.*, m.main_field, m.main_field_frac, m.main_subfield, m.main_subfield_frac,
               v.first_year, v.last_year,
               mt.display_name, mt.orcid, mt.oa_h_index, mt.oa_works_count, mt.oa_cited_by_count,
               mt.last_inst, mt.last_country
        FROM _as s
        LEFT JOIN _main m USING (author_idx)
        LEFT JOIN parquet_scan('{p.author_volume}') v USING (author_idx)
        LEFT JOIN _meta mt USING (author_idx)
    """)
    con.execute(f"COPY (SELECT *, {rank_measures()} FROM _full) "
                f"TO '{p.author_v}' (FORMAT PARQUET)")
    s = con.execute(f"""
        SELECT COUNT(*), COUNT(rank_pubv_frac), COUNT(rank_cv_frac_ns), COUNT(rank_cv_frac_25ns),
               MEDIAN(self_share)
        FROM parquet_scan('{p.author_v}')
    """).fetchone()
    print(f'  authors {s[0]:,}; ranked pubv {s[1]:,}, cv ns {s[2]:,}, cv 2025 ns {s[3]:,}; '
          f'median self-share {s[4]:.3f}')
    print(f'Total [{time.time()-t0:.0f}s]')


def main() -> None:
    ap = argparse.ArgumentParser()
    add_path_args(ap)
    args = ap.parse_args()
    p = resolve_paths(args)
    inputs = [p.author_works, p.author_volume, p.work_scores, p.work_subfields]
    if not guard.ensure_fresh(p.author_v, *inputs, script=__file__,
                              auto_yes=args.yes, label='author_v'):
        return
    t0 = time.time()
    build(p, connect())
    guard.record_build(p.author_v, *inputs, script=__file__, build_seconds=time.time() - t0)


if __name__ == '__main__':
    main()
