"""
build_author_works.py — R1: author × work table and author volume for the corpus.

For every corpus work (a work_idx in flat_works) the OpenAlex authorships give the
authors; one row per DISTINCT (author_idx, work_idx). Work type comes from the
compact works table, authors_count from flat_works (OpenAlex value; the corpus
keeps authors_count <= 29).

Volume baseline (Stanford): an author is eligible when they have >= MIN_WORKS
corpus works of type article, review or conference-paper.

Outputs (WORKING/researchers/):
  author_works_{ymin}_{ymax}.parquet
      author_idx, work_idx, publication_year, type, authors_count, stanford_type (bool)
  author_volume_{ymin}_{ymax}.parquet
      author_idx, n_works, n_works_st, first_year, last_year, eligible

Usage:
  .venv/bin/python researchers/build_author_works.py \
      --working /home/lc/s/SpectralRankingGlobal_WORKING_Oct26 \
      --openalex /home/lc/m/openalex_oct26/parquet_converted
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from researchers.common import (MIN_WORKS, STANFORD_TYPES, add_path_args, connect,
                                resolve_paths)
from util import guard


def build_author_volume(con, author_works_tbl: str = '_aw', min_works: int = MIN_WORKS) -> None:
    """Create TEMP TABLE _vol from an author_works table (pure SQL; testable)."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _vol AS
        SELECT author_idx,
               COUNT(*)                                        AS n_works,
               COUNT(*) FILTER (WHERE stanford_type)           AS n_works_st,
               MIN(publication_year)                           AS first_year,
               MAX(publication_year)                           AS last_year,
               COUNT(*) FILTER (WHERE stanford_type) >= {min_works} AS eligible
        FROM {author_works_tbl}
        GROUP BY author_idx
    """)


def build(p, con) -> None:
    t0 = time.time()
    types = ', '.join(f"'{t}'" for t in STANFORD_TYPES)

    print('Corpus works from flat_works ...')
    con.execute(f"""
        CREATE TEMP TABLE _cw AS
        SELECT work_idx, ANY_VALUE(publication_year) AS publication_year,
               ANY_VALUE(authors_count) AS authors_count
        FROM parquet_scan('{p.flat_works}')
        GROUP BY work_idx
    """)
    print(f"  {con.execute('SELECT COUNT(*) FROM _cw').fetchone()[0]:,} works  [{time.time()-t0:.0f}s]")

    t1 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _cwt AS
        SELECT c.work_idx, c.publication_year, c.authors_count, w.type,
               w.type IN ({types}) AS stanford_type
        FROM _cw c
        JOIN parquet_scan('{p.works_glob}') w USING (work_idx)
    """)
    con.execute('DROP TABLE _cw')
    print(f"  types attached: {con.execute('SELECT COUNT(*) FROM _cwt').fetchone()[0]:,}  [{time.time()-t1:.0f}s]")

    t1 = time.time()
    print('Author × work from compact authorships ...')
    con.execute(f"""
        CREATE TEMP TABLE _aw AS
        SELECT DISTINCT a.author_idx, a.work_idx, c.publication_year, c.type,
               c.authors_count, c.stanford_type
        FROM parquet_scan('{p.authorships_glob}') a
        JOIN _cwt c USING (work_idx)
        WHERE a.author_idx IS NOT NULL
    """)
    n = con.execute('SELECT COUNT(*) FROM _aw').fetchone()[0]
    print(f'  {n:,} author×work rows  [{time.time()-t1:.0f}s]')

    con.execute(f"COPY (SELECT * FROM _aw ORDER BY author_idx) TO '{p.author_works}' (FORMAT PARQUET)")

    build_author_volume(con)
    con.execute(f"COPY _vol TO '{p.author_volume}' (FORMAT PARQUET)")
    n_a, n_e = con.execute('SELECT COUNT(*), SUM(eligible::INT) FROM _vol').fetchone()
    print(f'  {n_a:,} authors; {n_e:,} eligible (>= {MIN_WORKS} article/review/conference-paper)')
    print(f'Total [{time.time()-t0:.0f}s]')


def main() -> None:
    ap = argparse.ArgumentParser()
    add_path_args(ap)
    args = ap.parse_args()
    p = resolve_paths(args)
    inputs = [p.flat_works]
    if not guard.ensure_fresh(p.author_works, *inputs, script=__file__,
                              auto_yes=args.yes, label='author_works'):
        return
    t0 = time.time()
    build(p, connect())
    for out in (p.author_works, p.author_volume):
        guard.record_build(out, *inputs, script=__file__, build_seconds=time.time() - t0)


if __name__ == '__main__':
    main()
