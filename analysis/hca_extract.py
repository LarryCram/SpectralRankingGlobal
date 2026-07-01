"""
hca_extract.py — Build OAX works → authors list for HCR matching.

Pipeline (each file built once, skipped if present):
  1. WORKING/filtered_works_topics.parquet  — (work × topic), with field_idx
  2. WORKING/hcw_flat_works.parquet         — (work × field), with field_share
  3. WORKING/hcw_works.parquet             — top-1% HCW per (field × year)
  4. WORKING/hcw_authors.parquet           — (author × field) with metadata
  5. WORKING/hcw_authorships.parquet       — (work_idx, author_idx) for HCW works

Works filter (stage 1):
  Source: OPENALEX/parquet/works/*.parquet, publication_year 2000–2025.
  Inclusion criteria (all must hold):
    - cited_by_count > (2027 − publication_year)   [≥1 citation per year of life]
    - is_paratext = false  AND  is_retracted = false
    - type ∈ {article, book, book-chapter, dissertation, letter, preprint, report, review}
    - title IS NOT NULL
  Title deduplication: where multiple works share an identical title, only the
    earliest (lowest publication_year, then lowest work_idx) is retained.
  Field assignment: topics are unnested and joined to topics.parquet; each work
    gets one row per OA field.  field_share = sum(topic scores in field) /
    sum(all topic scores for the work) — normalised propensity weight.

HCW definition (stage 3):
  Top 1% of cited_by_count per (field_idx, publication_year) within hcw_flat_works.
  Works with > 30 authors are excluded (hyper-authored papers distort name matching).

Per-field threshold (applied in hca_match.py, not here):
  thresh(field) = min n such that cumul%(n_hcw ≤ n) ≥ 90%.

Output schemas:
  hcw_authors.parquet:
    row_idx, author_idx, field_idx, n_hcw,
    display_name, h_index, works_count, cited_by_count, orcid,
    inst_name, inst_country   (modal institution across HCW authorships for that field)
    row_idx is a stable 1-based integer key.

  hcw_authorships.parquet:
    work_idx, author_idx
    All (work, author) pairs for HCW works; used for co-author overlap analysis.

Usage:
  .venv/bin/python analysis/hca_extract.py
"""

import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

MAX_AUTHORS = 30   # works with more authors than this are excluded (HEP/consortia)

# Winsorise citation counts at the 99.9th percentile per (field, year) before
# computing the 99th-percentile HCW threshold.  Prevents a handful of
# extreme-outlier papers from inflating the cut-off for everyone else.
# Set to False to revert to the raw-citation p99 (original behaviour).
WINSORISE = False
WINSORISE_PCTILE = 0.999   # cap ceiling percentile (only used when WINSORISE=True)

# Per-field HCW percentile overrides.  Fields not listed use HCW_DEFAULT_PCTILE.
# Mathematics (26) uses 0.98 (top 2%) because OAX field 26 is broader than
# Clarivate's Mathematics category, raising the p99 threshold above what
# Clarivate regards as highly cited.
HCW_DEFAULT_PCTILE = 0.99
HCW_FIELD_PCTILE: dict[int, float] = {
    26: 0.98,   # Mathematics
}

# ── DuckDB connection helper ──────────────────────────────────────────────────

def _con(working: Path) -> duckdb.DuckDBPyConnection:
    tmp = working.parent / '.tmp'
    tmp.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='24GB'; SET threads=8; "
                f"SET temp_directory='{tmp}'; SET preserve_insertion_order=false")
    return con


# ── Stage 1: filtered_works_topics ───────────────────────────────────────────

def build_filtered_works_topics(oax: Path, out: Path) -> None:
    """
    One row per (work × topic) after applying the works filter.
    Columns: work_idx, publication_year, cited_by_count, authors_count,
             institutions_distinct_count, field_score, field_idx, field_name.
    Skipped if the file already exists.
    """
    if out.exists():
        n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
        print(f'  filtered_works_topics exists  ({n:,} rows) — skipping')
        return

    print('  Building filtered_works_topics.parquet ...')
    t0 = time.time()
    con = _con(out.parent)
    con.execute(f"""
    COPY (
      WITH
      dedup_works AS (
        SELECT work_idx, title, publication_year, cited_by_count,
               authors_count, institutions_distinct_count
        FROM '{oax}/works/*.parquet'
        WHERE cited_by_count > (2027 - publication_year)
          AND title IS NOT NULL
          AND is_paratext  = false
          AND is_retracted = false
          AND list_contains(
                ['book','letter','dissertation','preprint',
                 'review','report','article','book-chapter'], type)
          AND publication_year BETWEEN 2000 AND 2025
        QUALIFY DENSE_RANK() OVER (
          PARTITION BY title ORDER BY publication_year ASC, work_idx ASC
        ) = 1
      ),
      filtered_works AS (
        SELECT dw.work_idx, dw.title, dw.publication_year, dw.cited_by_count,
               dw.authors_count, dw.institutions_distinct_count,
               unnest(wt.topics) AS topic
        FROM dedup_works dw
        JOIN '{oax}/work_topics/*.parquet' wt USING (work_idx)
      )
      SELECT DISTINCT
        fw.work_idx, fw.publication_year, fw.cited_by_count,
        fw.authors_count, fw.institutions_distinct_count,
        topic.score             AS field_score,
        t.field.id[29:]::BIGINT AS field_idx,
        t.field.display_name    AS field_name
      FROM filtered_works fw
      JOIN '{oax}/topics.parquet' t ON topic.topic_idx = t.topic_idx
    ) TO '{out}' (FORMAT PARQUET)
    """)
    con.close()
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    w = (duckdb.execute(f"SELECT COUNT(DISTINCT work_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  filtered_works_topics: {n:,} rows, {w:,} works  [{time.time()-t0:.0f}s]')


# ── Stage 2: hcw_flat_works ───────────────────────────────────────────────────

def build_hcw_flat_works(topics_path: Path, out: Path) -> None:
    """
    One row per (work × field) with normalised field_share.
    Columns: work_idx, publication_year, cited_by_count, authors_count,
             institutions_distinct_count, field_idx, field_name, field_share.
    Skipped if the file already exists.
    """
    if out.exists():
        n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
        print(f'  hcw_flat_works exists  ({n:,} rows) — skipping')
        return

    print('  Building hcw_flat_works.parquet ...')
    t0 = time.time()
    con = _con(out.parent)
    con.execute(f"""
    COPY (
      WITH shares AS (
        SELECT work_idx, publication_year, cited_by_count,
               authors_count, institutions_distinct_count,
               field_idx, field_name,
               field_score / SUM(field_score) OVER (PARTITION BY work_idx) AS field_share
        FROM '{topics_path}'
      )
      SELECT work_idx, publication_year, cited_by_count,
             authors_count, institutions_distinct_count,
             field_idx, field_name,
             ROUND(SUM(field_share), 3) AS field_share
      FROM shares
      WHERE authors_count <= {MAX_AUTHORS}
      GROUP BY work_idx, publication_year, cited_by_count,
               authors_count, institutions_distinct_count,
               field_idx, field_name
    ) TO '{out}' (FORMAT PARQUET)
    """)
    con.close()
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    w = (duckdb.execute(f"SELECT COUNT(DISTINCT work_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_flat_works: {n:,} rows, {w:,} works  [{time.time()-t0:.0f}s]')


# ── Stage 3: hcw_works ────────────────────────────────────────────────────────

def build_hcw_works(flat_path: Path, out: Path) -> None:
    """
    Top-1% of cited_by_count per (field_idx, publication_year) from hcw_flat_works.
    Columns: work_idx, publication_year, cited_by_count, field_idx, field_name, field_share.
    Skipped if the file already exists.
    """
    if out.exists():
        n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
        w = (duckdb.execute(f"SELECT COUNT(DISTINCT work_idx) FROM '{out}'").fetchone() or (0,))[0]
        print(f'  hcw_works exists  ({n:,} rows, {w:,} works) — skipping')
        return

    print('  Building hcw_works.parquet ...')
    t0 = time.time()
    con = _con(out.parent)
    # DuckDB requires constant percentile literals — build one branch per unique
    # percentile value, UNION ALL them to form the threshold table.
    from collections import defaultdict
    pctile_to_fields: dict[float, list[int]] = defaultdict(list)
    for fid, p in HCW_FIELD_PCTILE.items():
        pctile_to_fields[p].append(fid)
    # default branch covers all fields not in HCW_FIELD_PCTILE
    override_ids = list(HCW_FIELD_PCTILE.keys())
    branches = []
    for p, fids in pctile_to_fields.items():
        id_list = ', '.join(str(f) for f in fids)
        branches.append(f"""
        SELECT field_idx, publication_year,
               PERCENTILE_CONT({p}) WITHIN GROUP (ORDER BY cited_by_count) AS threshold
        FROM '{flat_path}'
        WHERE field_idx IN ({id_list})
        GROUP BY field_idx, publication_year""")
    excl = ', '.join(str(f) for f in override_ids) if override_ids else '0'
    branches.append(f"""
        SELECT field_idx, publication_year,
               PERCENTILE_CONT({HCW_DEFAULT_PCTILE}) WITHIN GROUP (ORDER BY cited_by_count) AS threshold
        FROM '{flat_path}'
        WHERE field_idx NOT IN ({excl})
        GROUP BY field_idx, publication_year""")
    th_union = ' UNION ALL '.join(branches)
    threshold_sql = f"""
      WITH th AS ({th_union})
      SELECT fw.work_idx, fw.publication_year, fw.cited_by_count,
             fw.field_idx, fw.field_name, fw.field_share
      FROM '{flat_path}' fw
      JOIN th ON fw.field_idx = th.field_idx
             AND fw.publication_year = th.publication_year
      WHERE fw.cited_by_count >= th.threshold
    """
    con.execute(f"COPY ({threshold_sql}) TO '{out}' (FORMAT PARQUET)")
    con.close()
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    w = (duckdb.execute(f"SELECT COUNT(DISTINCT work_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_works: {n:,} rows, {w:,} works  [{time.time()-t0:.0f}s]')


# ── Stage 4: hcw_authors ──────────────────────────────────────────────────────

def build_hcw_authors(hcw_path: Path, oax: Path, out: Path) -> None:
    """
    Join HCW works to authorships; aggregate to (author × field) with metadata.
    Works with > MAX_AUTHORS authors are excluded before counting.
    Skipped if the file already exists.
    """
    if out.exists():
        n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
        print(f'  hcw_authors exists  ({n:,} rows) — skipping')
        return

    print('  Building hcw_authors.parquet ...')
    t0 = time.time()
    con = _con(out.parent)

    con.execute(f"""
    CREATE TEMP TABLE _hcw AS
    SELECT work_idx, field_idx FROM '{hcw_path}'
    """)

    con.execute(f"""
    CREATE TEMP TABLE _inst AS
    SELECT institution_idx, display_name AS institution_name
    FROM '{oax}/institutions.parquet'
    """)

    con.execute(f"""
    CREATE TEMP TABLE _auth_raw AS
    SELECT h.field_idx, a.author_idx, a.work_idx,
           il.institution_name, a.country_code
    FROM '{oax}/authorships/*.parquet' a
    JOIN _hcw h ON h.work_idx = a.work_idx
    LEFT JOIN _inst il ON il.institution_idx = a.institution_idx
    WHERE a.author_idx IS NOT NULL
    """)
    n_raw = (con.execute('SELECT COUNT(*) FROM _auth_raw').fetchone() or (0,))[0]
    print(f'    authorships × HCW: {n_raw:,}  [{time.time()-t0:.0f}s]')

    con.execute("""
    CREATE TEMP TABLE _counts AS
    SELECT field_idx, author_idx, COUNT(DISTINCT work_idx) AS n_hcw
    FROM _auth_raw GROUP BY field_idx, author_idx
    """)

    con.execute("""
    CREATE TEMP TABLE _inst_mode AS
    WITH _ic AS (
        SELECT field_idx, author_idx, institution_name, country_code, COUNT(*) AS n
        FROM _auth_raw WHERE institution_name IS NOT NULL
        GROUP BY field_idx, author_idx, institution_name, country_code
    )
    SELECT field_idx, author_idx,
           institution_name AS inst_name,
           country_code     AS inst_country
    FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY field_idx, author_idx ORDER BY n DESC
        ) AS rn FROM _ic
    ) WHERE rn = 1
    """)

    con.execute(f"""
    CREATE TEMP TABLE _base AS
    SELECT c.field_idx, c.author_idx, c.n_hcw,
           m.inst_name, m.inst_country
    FROM _counts c LEFT JOIN _inst_mode m USING (field_idx, author_idx)
    """)

    con.execute(f"""
    CREATE TEMP TABLE _meta AS
    SELECT a.author_idx, a.display_name, a.h_index,
           a.works_count, a.cited_by_count, a.orcid
    FROM '{oax}/authors/*.parquet' a
    JOIN (SELECT DISTINCT author_idx FROM _base) ids ON a.author_idx = ids.author_idx
    """)
    print(f'    author metadata done  [{time.time()-t0:.0f}s]')

    con.execute(f"""
    COPY (
      SELECT ROW_NUMBER() OVER () AS row_idx,
             b.field_idx, b.author_idx, b.n_hcw,
             m.display_name, m.h_index, m.works_count, m.cited_by_count, m.orcid,
             b.inst_name, b.inst_country
      FROM _base b JOIN _meta m USING (author_idx)
      ORDER BY b.field_idx, b.n_hcw DESC
    ) TO '{out}' (FORMAT PARQUET)
    """)
    con.close()
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    a = (duckdb.execute(f"SELECT COUNT(DISTINCT author_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_authors: {n:,} rows, {a:,} distinct authors  [{time.time()-t0:.0f}s]')


# ── Stage 5: hcw_authorships ─────────────────────────────────────────────────

def build_hcw_authorships(hcw_path: Path, oax: Path, out: Path) -> None:
    """
    All (work_idx, author_idx) pairs for HCW works.
    Used for co-author overlap analysis in the clustering pipeline.
    Skipped if the file already exists.
    """
    if out.exists():
        n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
        print(f'  hcw_authorships exists  ({n:,} rows) — skipping')
        return

    print('  Building hcw_authorships.parquet ...')
    t0 = time.time()
    con = _con(out.parent)
    con.execute(f"""
    COPY (
      SELECT DISTINCT a.work_idx, a.author_idx
      FROM '{oax}/authorships/*.parquet' a
      JOIN (SELECT DISTINCT work_idx FROM '{hcw_path}') h USING (work_idx)
      WHERE a.author_idx IS NOT NULL
    ) TO '{out}' (FORMAT PARQUET)
    """)
    con.close()
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_authorships: {n:,} rows  [{time.time()-t0:.0f}s]')


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    paths = load_config()
    oa    = paths.openalex / 'parquet'
    w     = paths.working

    topics_path      = w / 'filtered_works_topics.parquet'
    flat_path        = w / 'hcw_flat_works.parquet'
    hcw_path         = w / 'hcw_works.parquet'
    authors_path     = w / 'hcw_authors.parquet'
    authorships_path = w / 'hcw_authorships.parquet'

    print('Stage 1: filtered_works_topics')
    build_filtered_works_topics(oa, topics_path)

    print('Stage 2: hcw_flat_works')
    build_hcw_flat_works(topics_path, flat_path)

    print('Stage 3: hcw_works  (top 1% per field × year)')
    build_hcw_works(flat_path, hcw_path)

    print('Stage 4: hcw_authors')
    build_hcw_authors(hcw_path, oa, authors_path)

    print('Stage 5: hcw_authorships')
    build_hcw_authorships(hcw_path, oa, authorships_path)

    print('\nDone.')
    n, a, f = duckdb.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT author_idx), COUNT(DISTINCT field_idx)
        FROM '{authors_path}'
    """).fetchone()
    print(f'  hcw_authors: {n:,} (author × field) rows, {a:,} distinct authors, {f} fields')
    n2 = duckdb.execute(f"SELECT COUNT(*) FROM '{authorships_path}'").fetchone()[0]
    print(f'  hcw_authorships: {n2:,} (work × author) rows')


if __name__ == '__main__':
    main()
