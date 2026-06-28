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
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

YEAR_LO = 2000
YEAR_HI = 2025


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


RANKS = [1, 500, 1000, 1500, 2000]


def _print_unit_rank_table(db, fw: str, top_p: str, unit: str) -> None:
    """
    Print one row per field showing cumulative % of total topic weight up to each rank cutoff.

    unit='source'      weight = SUM(field_weight)               per (field, source)
    unit='institution' weight = SUM(field_weight * inst_weight)  per (field, institution)

    pct_N = cumulative % of total topic weight for units ranked 1..N.
    total = grand total topic weight across all units in the field.
    """
    assert unit in ('source', 'institution')
    col = 'source_idx' if unit == 'source' else 'institution_idx'
    rank_cases = ',\n'.join(
        f"           ROUND(100.0 * SUM(CASE WHEN rnk <= {r} THEN total_weight END)"
        f" / SUM(total_weight), 1) AS pct_{r}"
        for r in RANKS
    )
    # Sources: deduplicate on work before summing — field_weight is per-work, not
    # per-institution, so without DISTINCT a work with N institutions contributes
    # N × field_weight. Institutions use field_weight * inst_weight which already
    # sums to field_weight across institutions, so no dedup needed.
    if unit == 'source':
        inner = f"""
            SELECT field_idx, {col} AS unit_idx,
                   SUM(field_weight) AS total_weight
            FROM (SELECT DISTINCT work_idx, source_idx, field_idx, field_weight FROM '{fw}')
            GROUP BY field_idx, {col}"""
    else:
        inner = f"""
            SELECT field_idx, {col} AS unit_idx,
                   SUM(field_weight * inst_weight) AS total_weight
            FROM '{fw}'
            GROUP BY field_idx, {col}"""
    section(f"{unit.capitalize()} units — cumulative % of total topic weight up to rank cutoff")
    db.sql(f"""
        WITH unit_weights AS ({inner}
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
                   {rank_cases},
                   ROUND(SUM(total_weight), 0) AS total
            FROM ranked
            GROUP BY field_idx
        ),
        field_names AS (
            SELECT DISTINCT field_idx, field_name FROM '{top_p}'
        )
        SELECT fn.field_name,
               p.pct_1,
               p.pct_500,
               p.pct_1000,
               p.pct_1500,
               p.pct_2000,
               p.total
        FROM pivoted p
        JOIN field_names fn ON p.field_idx = fn.field_idx
        ORDER BY p.pct_1000 DESC NULLS LAST
    """).show(max_width=160)


def _print_country_report(db, fw: str) -> None:
    section("Country report — distinct works per Leiden group (top 10 + bottom 10 by total)")

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _cw AS
        SELECT DISTINCT work_idx, leiden_idx, country_code
        FROM '{fw}'
        WHERE country_code IS NOT NULL
    """)

    raw = db.sql("""
        SELECT country_code,
               COUNT(DISTINCT CASE WHEN leiden_idx = 1 THEN work_idx END) AS l1,
               COUNT(DISTINCT CASE WHEN leiden_idx = 2 THEN work_idx END) AS l2,
               COUNT(DISTINCT CASE WHEN leiden_idx = 3 THEN work_idx END) AS l3,
               COUNT(DISTINCT CASE WHEN leiden_idx = 4 THEN work_idx END) AS l4,
               COUNT(DISTINCT CASE WHEN leiden_idx = 5 THEN work_idx END) AS l5,
               COUNT(DISTINCT work_idx)                                    AS total
        FROM _cw
        GROUP BY country_code
        ORDER BY total DESC
    """).df()

    field_totals = db.sql("""
        SELECT leiden_idx, COUNT(DISTINCT work_idx) AS field_total
        FROM _cw
        GROUP BY leiden_idx
        ORDER BY leiden_idx
    """).df().set_index('leiden_idx')['field_total'].to_dict()

    grand_total = db.sql(
        "SELECT COUNT(DISTINCT work_idx) AS n FROM _cw"
    ).fetchone()[0]

    db.execute("DROP TABLE IF EXISTS _cw")

    leiden_cols = {
        'l1': ('Maths&CS',   1),
        'l2': ('Phys&Eng',   2),
        'l3': ('Life&Earth', 3),
        'l4': ('Biomed',     4),
        'l5': ('Soc&Hum',   5),
    }

    def fmt(count, denom):
        pct = 100.0 * count / denom if denom else 0
        return f"{count:,} ({pct:.1f}%)"

    out = pd.DataFrame()
    out['country'] = raw['country_code']
    for col, (label, lid) in leiden_cols.items():
        ft = field_totals.get(lid, 1)
        out[label] = raw[col].apply(lambda c: fmt(c, ft))
    out['total'] = raw['total'].apply(lambda c: fmt(c, grand_total))

    pd.set_option('display.width', 160)
    pd.set_option('display.max_colwidth', 18)

    au_pos = out.index[out['country'] == 'AU']
    n_top  = (au_pos[0] + 3) if len(au_pos) else 10

    print(f"\nTop {n_top} (through AU + 2):")
    print(out.head(n_top).to_string(index=False))
    print("\nBottom 10:")
    print(out.tail(10).to_string(index=False))


def _print_top_units(db, fw: str, src_p: str, inst_p: str) -> None:
    section("Top 5 sources and institutions by total unit topic weight per field")

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _src_w AS
        SELECT field_idx, source_idx AS unit_idx,
               SUM(field_weight) AS total_weight
        FROM (SELECT DISTINCT work_idx, source_idx, field_idx, field_weight FROM '{fw}')
        GROUP BY field_idx, source_idx
    """)

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _inst_w AS
        SELECT field_idx, institution_idx AS unit_idx,
               SUM(field_weight * inst_weight) AS total_weight
        FROM '{fw}'
        GROUP BY field_idx, institution_idx
    """)

    pd.set_option('display.max_rows', None)
    pd.set_option('display.width', 160)
    pd.set_option('display.max_colwidth', 60)

    print("\n── Sources ──")
    src_df = db.sql(f"""
        WITH ranked AS (
            SELECT field_idx, unit_idx, total_weight,
                   ROW_NUMBER() OVER (PARTITION BY field_idx ORDER BY total_weight DESC) AS rnk
            FROM _src_w
        )
        SELECT r.field_idx,
               r.rnk                                        AS rank,
               s.display_name                               AS source,
               ROUND(r.total_weight, 0)                     AS weight
        FROM ranked r
        JOIN '{src_p}' s
          ON r.unit_idx = CAST(REGEXP_REPLACE(s.id, 'https://openalex.org/S', '') AS BIGINT)
        WHERE r.rnk <= 5
        ORDER BY r.field_idx, r.rnk
    """).df()
    print(src_df.to_string(index=False))

    print("\n── Institutions ──")
    inst_df = db.sql(f"""
        WITH ranked AS (
            SELECT field_idx, unit_idx, total_weight,
                   ROW_NUMBER() OVER (PARTITION BY field_idx ORDER BY total_weight DESC) AS rnk
            FROM _inst_w
        )
        SELECT r.field_idx,
               r.rnk                                        AS rank,
               i.display_name                               AS institution,
               ROUND(r.total_weight, 0)                     AS weight
        FROM ranked r
        JOIN '{inst_p}' i
          ON r.unit_idx = CAST(REGEXP_REPLACE(i.id, 'https://openalex.org/I', '') AS BIGINT)
        WHERE r.rnk <= 5
        ORDER BY r.field_idx, r.rnk
    """).df()
    print(inst_df.to_string(index=False))

    db.execute("DROP TABLE IF EXISTS _src_w")
    db.execute("DROP TABLE IF EXISTS _inst_w")


def main():
    paths   = load_config()
    fw      = str(paths.working / f"flat_works_{YEAR_LO}_{YEAR_HI}.parquet")
    src_p   = f"{paths.openalex}/parquet/sources.parquet"
    inst_p  = f"{paths.openalex}/parquet/institutions.parquet"
    top_p   = f"{paths.openalex}/parquet/work_topics/*.parquet"

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

        # ── 7. Top 5 units per field ──────────────────────────────────────────
        _print_top_units(db, fw, src_p, inst_p)

        # ── 8. Country report ─────────────────────────────────────────────────
        _print_country_report(db, fw)

        # ── 9. Cross-field pairs ──────────────────────────────────────────────
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
