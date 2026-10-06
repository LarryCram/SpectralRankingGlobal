"""
util/build_md_journal_whitelist.py — Extract Multidisciplinary (MD) journals
from the official ERA 2018 Journal List and map them to OpenAlex source_idx.

Output:
  data/md_journal_whitelist.parquet (schema: source_idx BIGINT, era_journal_id INT, era_title VARCHAR, openalex_name VARCHAR, match_method VARCHAR)
"""

from pathlib import Path
import duckdb
import openpyxl


def build_whitelist(era_journal_xlsx: Path, sources_parquet: Path, out_path: Path):
    print(f"Loading ERA 2018 Journal List from: {era_journal_xlsx}")
    wb = openpyxl.load_workbook(era_journal_xlsx, read_only=True)
    sheet = wb["ERA 2018 Journal List"]

    md_journals = {}
    for row in sheet.iter_rows(min_row=2, values_only=True):
        era_id, title, foreign_title, f1, n1, f2, n2, f3, n3 = row[:9]
        if f1 == 'MD' or f2 == 'MD' or f3 == 'MD':
            issns = [str(x).strip().replace('-', '').upper() for x in row[9:16] if x and str(x).strip()]
            md_journals[era_id] = {'title': str(title).strip(), 'issns': issns}

    print(f"Found {len(md_journals)} MD journals in ERA 2018 list.")

    con = duckdb.connect()
    con.execute("""
        CREATE TEMP TABLE era_md (era_id INT, title VARCHAR);
        CREATE TEMP TABLE era_md_issn (era_id INT, clean_issn VARCHAR);
    """)
    for era_id, d in md_journals.items():
        con.execute("INSERT INTO era_md VALUES (?, ?)", [era_id, d['title']])
        for issn in d['issns']:
            con.execute("INSERT INTO era_md_issn VALUES (?, ?)", [era_id, issn])

    out_path.parent.mkdir(parents=True, exist_ok=True)

    create_sql = f"""
        COPY (
            WITH src_issn AS (
                SELECT source_idx, display_name, 
                       REPLACE(UPPER(issn_val), '-', '') AS clean_issn
                FROM (
                    SELECT source_idx, display_name, UNNEST(issn) AS issn_val
                    FROM read_parquet('{sources_parquet}')
                    WHERE issn IS NOT NULL
                    UNION
                    SELECT source_idx, display_name, issn_l AS issn_val
                    FROM read_parquet('{sources_parquet}')
                    WHERE issn_l IS NOT NULL
                )
            ),
            issn_matches AS (
                SELECT DISTINCT era_md_issn.era_id, src_issn.source_idx, src_issn.display_name, 'issn' AS match_method
                FROM era_md_issn
                JOIN src_issn ON era_md_issn.clean_issn = src_issn.clean_issn
            ),
            unmatched_era AS (
                SELECT era_id, title FROM era_md WHERE era_id NOT IN (SELECT era_id FROM issn_matches)
            ),
            title_matches AS (
                SELECT DISTINCT u.era_id, s.source_idx, s.display_name, 'title' AS match_method
                FROM unmatched_era u
                JOIN read_parquet('{sources_parquet}') s
                  ON LOWER(TRIM(u.title)) = LOWER(TRIM(s.display_name))
            ),
            combined AS (
                SELECT * FROM issn_matches
                UNION ALL
                SELECT * FROM title_matches
            )
            SELECT DISTINCT 
                c.source_idx::BIGINT AS source_idx,
                c.era_id AS era_journal_id,
                m.title AS era_title,
                c.display_name AS openalex_name,
                c.match_method
            FROM combined c
            JOIN era_md m ON c.era_id = m.era_id
            ORDER BY c.source_idx
        ) TO '{out_path}' (FORMAT PARQUET)
    """
    con.execute(create_sql)
    print(f"Saved whitelist to: {out_path}")

    info = con.execute(f"""
        SELECT COUNT(*) AS total_rows,
               COUNT(DISTINCT source_idx) AS unique_sources,
               COUNT(DISTINCT era_journal_id) AS matched_era_journals
        FROM read_parquet('{out_path}')
    """).fetchone()
    print(f"Matched: {info[2]} / {len(md_journals)} ERA journals -> {info[1]} OpenAlex sources ({info[0]} total rows).")


def main():
    root = Path(__file__).parent.parent
    era_xlsx = Path("/home/lc/Projects_Antigravity/SmallProjects/data_persisted/ERA 2018 Journal List.xlsx")
    sources_pq = Path("/media/m-drive/openalex_jul26/parquet_converted/sources.parquet")
    out_pq = root / "data" / "md_journal_whitelist.parquet"

    build_whitelist(era_xlsx, sources_pq, out_pq)


if __name__ == '__main__':
    main()
