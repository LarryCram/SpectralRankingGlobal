"""
build_flat_works.py — Build the flat works table: one row per (work × institution × field).

Filters applied:
  - publication_year 2016–2025
  - type in ('article', 'review')
  - is_paratext = false, is_retracted = false
  - source type in ('journal', 'conference', 'book series')
  - institution type in ('education', 'nonprofit', 'government', 'healthcare', 'other')
    (excludes company, funder, archive, etc.)
  - work has at least one qualifying institutional authorship
  - work has at least one topic assignment

Outputs:
  WORKING/flat_works_{YEAR_MIN}_{YEAR_MAX}.parquet
  WORKING/corpus_references_{YEAR_MIN}_{YEAR_MAX}.parquet  — (citer_idx, cited_idx)
    pairs where both works appear in flat_works; avoids scanning all OA references
    (~500M rows) on every Stage 3 run.

Schema:
  work_idx               BIGINT
  publication_year       BIGINT
  source_idx             BIGINT
  institution_idx        BIGINT
  inst_weight            DOUBLE   -- author-fractional: SUM(1/n_authors/n_inst_per_author)
  direct_inst_weight     DOUBLE   -- institution-fractional: 1/n_qualifying_institutions
  field_idx              BIGINT
  field_weight           DOUBLE   -- score-normalised, sums to 1 per work across fields
  referenced_works_count BIGINT
"""

import sys
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

YEAR_MIN = 2016
YEAR_MAX = 2025

SOURCE_TYPES      = ('journal', 'conference', 'book series')
WORK_TYPES        = ('article', 'review')
INSTITUTION_TYPES = ('education', 'nonprofit', 'government', 'healthcare', 'other')


def build_flat_works(db: duckdb.DuckDBPyConnection,
                     works_path: str,
                     sources_path: str,
                     authorships_path: str,
                     institutions_path: str,
                     topics_path: str,
                     out_path: str,
                     year_min: int = YEAR_MIN,
                     year_max: int = YEAR_MAX) -> int:
    """
    Build flat works table and write to out_path as parquet.
    Returns row count.
    """
    src_types  = ', '.join(f"'{t}'" for t in SOURCE_TYPES)
    work_types = ', '.join(f"'{t}'" for t in WORK_TYPES)
    inst_types = ', '.join(f"'{t}'" for t in INSTITUTION_TYPES)

    # ── pass 1: small lookup tables and filtered works ────────────────────────

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _valid_inst AS
        SELECT CAST(REGEXP_REPLACE(id, 'https://openalex.org/I', '') AS BIGINT)
                   AS institution_idx
        FROM '{institutions_path}'
        WHERE type IN ({inst_types})
    """)

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _fw AS
        SELECT work_idx,
               source_id         AS source_idx,
               publication_year,
               referenced_works_count
        FROM '{works_path}'
        WHERE publication_year BETWEEN {year_min} AND {year_max}
          AND type IN ({work_types})
          AND is_paratext           = false
          AND is_retracted          = false
          AND referenced_works_count > 0
          AND source_id IN (
              SELECT CAST(REGEXP_REPLACE(id, 'https://openalex.org/S', '') AS BIGINT)
              FROM '{sources_path}' WHERE type IN ({src_types})
          )
    """)

    # ── pass 2: one authorships scan → small _iw (work × institution) ─────────
    # _auth is NOT materialised; all aggregates are computed in a single CTE
    # pipeline so DuckDB streams authorships without building a large temp table.

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _iw AS
        WITH auth AS (
            SELECT a.work_idx, a.author_idx, a.institution_idx
            FROM '{authorships_path}' a
            WHERE a.work_idx       IN (SELECT work_idx FROM _fw)
              AND a.author_idx      IS NOT NULL
              AND a.institution_idx IN (SELECT institution_idx FROM _valid_inst)
        ),
        wac AS (
            SELECT work_idx, COUNT(DISTINCT author_idx)      AS n_authors
            FROM auth GROUP BY work_idx
        ),
        aic AS (
            SELECT work_idx, author_idx, COUNT(DISTINCT institution_idx) AS n_inst
            FROM auth GROUP BY work_idx, author_idx
        ),
        wic AS (
            SELECT work_idx, COUNT(DISTINCT institution_idx) AS n_institutions
            FROM auth GROUP BY work_idx
        )
        SELECT a.work_idx,
               a.institution_idx,
               SUM(1.0 / wac.n_authors / aic.n_inst) AS inst_weight,
               1.0 / ANY_VALUE(wic.n_institutions)    AS direct_inst_weight
        FROM auth a
        JOIN wac ON a.work_idx = wac.work_idx
        JOIN aic ON a.work_idx = aic.work_idx AND a.author_idx = aic.author_idx
        JOIN wic ON a.work_idx = wic.work_idx
        GROUP BY a.work_idx, a.institution_idx
    """)

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _top AS
        SELECT work_idx, field_idx, SUM(score) AS field_score
        FROM '{topics_path}'
        WHERE work_idx IN (SELECT work_idx FROM _fw)
        GROUP BY work_idx, field_idx
    """)

    # ── pass 3: join small tables and write output ────────────────────────────

    db.execute(f"""
        COPY (
        WITH
        work_total_scores AS (
            SELECT work_idx, SUM(field_score) AS total_score
            FROM _top GROUP BY work_idx
        ),
        fw_fields AS (
            SELECT t.work_idx,
                   t.field_idx,
                   t.field_score / wts.total_score AS field_weight
            FROM _top t
            JOIN work_total_scores wts ON t.work_idx = wts.work_idx
        )
        SELECT
            fw.work_idx,
            fw.publication_year,
            fw.source_idx,
            iw.institution_idx,
            iw.inst_weight,
            iw.direct_inst_weight,
            ff.field_idx,
            ff.field_weight,
            fw.referenced_works_count
        FROM _fw fw
        JOIN _iw      iw ON fw.work_idx = iw.work_idx
        JOIN fw_fields ff ON fw.work_idx = ff.work_idx
        ) TO '{out_path}' (FORMAT PARQUET)
    """)

    db.execute("DROP TABLE IF EXISTS _valid_inst")
    db.execute("DROP TABLE IF EXISTS _fw")
    db.execute("DROP TABLE IF EXISTS _iw")
    db.execute("DROP TABLE IF EXISTS _top")

    return db.execute(f"SELECT COUNT(*) FROM '{out_path}'").fetchone()[0]


def build_corpus_references(db: duckdb.DuckDBPyConnection,
                            fw_path: str,
                            refs_glob: str,
                            out_path: str) -> int:
    """
    Build corpus_references parquet: (citer_idx, cited_idx) pairs where both
    works appear in flat_works. Scans OA references once; result is reused by
    Stage 3 for all fields instead of repeating the full ~500M-row scan.

    Returns row count.
    """
    db.execute(f"""
        COPY (
            SELECT ref.citer_idx, ref.cited_idx
            FROM '{refs_glob}' ref
            WHERE ref.citer_idx IN (SELECT DISTINCT work_idx FROM '{fw_path}')
              AND ref.cited_idx  IN (SELECT DISTINCT work_idx FROM '{fw_path}')
        ) TO '{out_path}' (FORMAT PARQUET)
    """)
    return db.execute(f"SELECT COUNT(*) FROM '{out_path}'").fetchone()[0]


def main():
    paths = load_config()

    works_path        = f"{paths.openalex}/parquet/works/*.parquet"
    sources_path      = f"{paths.openalex}/parquet/sources.parquet"
    authorships_path  = f"{paths.openalex}/parquet/authorships/*.parquet"
    institutions_path = f"{paths.openalex}/parquet/institutions.parquet"
    topics_path       = f"{paths.openalex}/parquet/topics/*.parquet"
    refs_glob         = f"{paths.openalex}/parquet/references/*.parquet"
    fw_path           = str(paths.working / f"flat_works_{YEAR_MIN}_{YEAR_MAX}.parquet")
    cr_path           = str(paths.working / f"corpus_references_{YEAR_MIN}_{YEAR_MAX}.parquet")

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute("SET memory_limit = '56GB'")
        db.execute("SET preserve_insertion_order = false")

        if not Path(fw_path).exists():
            print(f"Building flat works table ({YEAR_MIN}–{YEAR_MAX}) ...")
            n = build_flat_works(db, works_path, sources_path, authorships_path,
                                 institutions_path, topics_path, fw_path)
            print(f"Wrote {n:,} rows → {fw_path}")
        else:
            n = db.execute(f"SELECT COUNT(*) FROM '{fw_path}'").fetchone()[0]
            print(f"flat_works exists: {n:,} rows  ({fw_path})")

        print(f"\nBuilding corpus references ...")
        import time
        t0 = time.time()
        nr = build_corpus_references(db, fw_path, refs_glob, cr_path)
        print(f"Wrote {nr:,} rows in {time.time()-t0:.0f}s → {cr_path}")


if __name__ == "__main__":
    main()
    print("FINISHED!")
