"""
summary_flat_works.py — Diagnostic summary of flat_works_{ymin}_{ymax}.parquet.

Reports:
  1. Work, source, institution counts (totals)
  2. Works by publication year
  3. Sources by type (journal / conference / book series)
  4. Institutions by type (education / nonprofit / government / other)
  5. Fields: works, sources, institutions per field
  6. Cross-field pairs: works contributing to both fields (top 20)
  7. Source unit rank table: total topic weight at ranks 1/500/1000/1500/2000 per field
  8. Institution unit rank table: same for institutions
"""

import sys
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

YEAR_MIN = 2016
YEAR_MAX = 2025


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


RANKS = [1, 500, 1000, 1500, 2000]


def _print_unit_rank_table(db, fw: str, top_p: str, unit: str) -> None:
    """
    Print one row per field showing total topic weight at each rank cutoff.

    unit='source'      weight = SUM(field_weight)               per (field, source)
    unit='institution' weight = SUM(field_weight * inst_weight)  per (field, institution)
    """
    assert unit in ('source', 'institution')
    col      = 'source_idx' if unit == 'source' else 'institution_idx'
    wt_expr  = 'field_weight' if unit == 'source' else 'field_weight * inst_weight'
    rank_cases = ',\n'.join(
        f"           MAX(CASE WHEN rnk = {r} THEN ROUND(total_weight, 2) END) AS rank_{r}"
        for r in RANKS
    )
    section(f"{unit.capitalize()} units — total topic weight at rank cutoffs")
    db.sql(f"""
        WITH unit_weights AS (
            SELECT field_idx, {col} AS unit_idx,
                   SUM({wt_expr}) AS total_weight
            FROM '{fw}'
            GROUP BY field_idx, {col}
        ),
        ranked AS (
            SELECT field_idx, unit_idx, total_weight,
                   ROW_NUMBER() OVER (
                       PARTITION BY field_idx ORDER BY total_weight DESC
                   ) AS rnk
            FROM unit_weights
        ),
        pivoted AS (
            SELECT field_idx,
                   {rank_cases}
            FROM ranked
            WHERE rnk IN ({', '.join(str(r) for r in RANKS)})
            GROUP BY field_idx
        ),
        n_above AS (
            SELECT field_idx, COUNT(*) AS n_units_200
            FROM unit_weights
            WHERE total_weight >= 200
            GROUP BY field_idx
        ),
        field_names AS (
            SELECT DISTINCT field_idx, field_name FROM '{top_p}'
        )
        SELECT fn.field_name,
               p.rank_1,
               p.rank_500,
               p.rank_1000,
               p.rank_1500,
               p.rank_2000,
               COALESCE(n.n_units_200, 0) AS n_units_200
        FROM pivoted p
        JOIN field_names fn ON p.field_idx = fn.field_idx
        LEFT JOIN n_above n ON p.field_idx = n.field_idx
        ORDER BY p.rank_1000 DESC NULLS LAST
    """).show(max_width=160)


def main():
    paths   = load_config()
    fw      = str(paths.working / f"flat_works_{YEAR_MIN}_{YEAR_MAX}.parquet")
    src_p   = f"{paths.openalex}/parquet/sources.parquet"
    inst_p  = f"{paths.openalex}/parquet/institutions.parquet"
    top_p   = f"{paths.openalex}/parquet/topics/*.parquet"

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute("SET memory_limit = '56GB'")

        # ── 1. Totals ─────────────────────────────────────────────────────────
        section("Totals")
        db.sql(f"""
            SELECT
                COUNT(DISTINCT work_idx)        AS n_works,
                COUNT(DISTINCT source_idx)      AS n_sources,
                COUNT(DISTINCT institution_idx) AS n_institutions,
                COUNT(DISTINCT field_idx)       AS n_fields,
                COUNT(*)                        AS n_rows
            FROM '{fw}'
        """).show()

        # ── 2. Works by year ──────────────────────────────────────────────────
        section("Works by publication year")
        db.sql(f"""
            SELECT publication_year,
                   COUNT(DISTINCT work_idx) AS n_works
            FROM '{fw}'
            GROUP BY publication_year
            ORDER BY publication_year
        """).show()

        # ── 3. Sources by type ────────────────────────────────────────────────
        section("Sources by type")
        db.sql(f"""
            SELECT s.type,
                   COUNT(DISTINCT fw.source_idx) AS n_sources,
                   COUNT(DISTINCT fw.work_idx)   AS n_works
            FROM '{fw}' fw
            JOIN '{src_p}' s
              ON fw.source_idx =
                 CAST(REGEXP_REPLACE(s.id, 'https://openalex.org/S', '') AS BIGINT)
            GROUP BY s.type
            ORDER BY n_works DESC
        """).show()

        # ── 4. Institutions by type ───────────────────────────────────────────
        section("Institutions by type")
        db.sql(f"""
            SELECT i.type,
                   COUNT(DISTINCT fw.institution_idx) AS n_institutions,
                   COUNT(DISTINCT fw.work_idx)        AS n_works
            FROM '{fw}' fw
            JOIN '{inst_p}' i
              ON fw.institution_idx =
                 CAST(REGEXP_REPLACE(i.id, 'https://openalex.org/I', '') AS BIGINT)
            GROUP BY i.type
            ORDER BY n_institutions DESC
        """).show()

        # ── 5. Fields ─────────────────────────────────────────────────────────
        section("Fields (works, sources, institutions per field)")
        db.sql(f"""
            WITH field_names AS (
                SELECT DISTINCT field_idx, field_name
                FROM '{top_p}'
            )
            SELECT fw.field_idx,
                   fn.field_name,
                   COUNT(DISTINCT fw.work_idx)        AS n_works,
                   COUNT(DISTINCT fw.source_idx)      AS n_sources,
                   COUNT(DISTINCT fw.institution_idx) AS n_institutions
            FROM '{fw}' fw
            JOIN field_names fn ON fw.field_idx = fn.field_idx
            GROUP BY fw.field_idx, fn.field_name
            ORDER BY n_works DESC
        """).show(max_width=120)

        # ── 6. Unit rank tables ───────────────────────────────────────────────
        _print_unit_rank_table(db, fw, top_p, 'source')
        _print_unit_rank_table(db, fw, top_p, 'institution')

        # ── 7. Cross-field pairs ───────────────────────────────────────────────
        section("Cross-field pairs: works contributing to both fields (top 20)")
        db.sql(f"""
            WITH work_fields AS (
                SELECT DISTINCT work_idx, field_idx
                FROM '{fw}'
            ),
            field_names AS (
                SELECT DISTINCT field_idx, field_name
                FROM '{top_p}'
            )
            SELECT
                na.field_name AS field_a,
                nb.field_name AS field_b,
                COUNT(DISTINCT a.work_idx) AS n_works
            FROM work_fields a
            JOIN work_fields b  ON a.work_idx  = b.work_idx
                                AND a.field_idx < b.field_idx
            JOIN field_names na ON a.field_idx = na.field_idx
            JOIN field_names nb ON b.field_idx = nb.field_idx
            GROUP BY na.field_name, nb.field_name
            ORDER BY n_works DESC
            LIMIT 20
        """).show(max_width=160)


if __name__ == "__main__":
    main()
    print("\nFINISHED!")
