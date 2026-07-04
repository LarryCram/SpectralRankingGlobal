"""
hca_extract.py — Build OAX works → authors list for HCR matching.

Pipeline (each file built once, skipped if present):
  1. WORKING/filtered_works_topics.parquet  — (work × topic), with field_idx
  2. WORKING/hcw_flat_works.parquet         — (work × field), with field_share
  3. WORKING/hcw_works.parquet             — top-1% HCW per (field × year)
  4. WORKING/hcw_authors.parquet           — (author × field) with metadata
  5. WORKING/hcw_authorships.parquet       — (work_idx, author_idx) for HCW works

Works filter (stage 1):
  Base work list is WORKING/flat_works_{YEAR_MIN}_{YEAR_MAX}.parquet (the project's
  master table, see pipeline/build_flat_works.py) — NOT a raw OA works scan. flat_works
  already applies: publication_year 2000–2025, type in the retained work-type list
  (settings.yaml, now article/review/book/book-chapter/dissertation/letter/preprint/report),
  is_paratext=false, is_retracted=false, title IS NOT NULL + title dedup (earliest by
  publication_year then work_idx), authors_count <= MAX_AUTHORS (29, in build_flat_works.py).
  This stage adds exactly one further filter of its own:
    - cited_by_count > (2027 − publication_year)   [≥1 citation per year of life]
  Field assignment: topics are unnested and joined to topics.parquet; each work
    gets one row per OA field.  field_share = sum(topic scores in field) /
    sum(all topic scores for the work) — normalised propensity weight.
  NOTE: this also means the HCA candidate pool now inherits flat_works' other
  membership rules it never had before — referenced_works_count > 0, source type in
  the retained set, and >=1 qualifying-institution-type authorship. See
  build_filtered_works_topics()'s docstring for detail.

HCW definition (stage 3):
  Top 1% of cited_by_count per (field_idx, publication_year), computed over the
  FULL 2000–2025 range within hcw_flat_works — a work published in any year can
  be a HCW; the percentile threshold is local to its own (field, year) group.
  Hyper-authored (>29-author) works are already excluded upstream by flat_works.

HCA qualification window (stage 4):
  n_hcw (an author's count of HCW, used as their HCA-qualification signal) counts
  ONLY HCW with publication_year BETWEEN HCA_YEAR_MIN and HCA_YEAR_MAX (2014–2024).
  This mirrors Clarivate's Highly Cited Researchers methodology, which counts an
  author's highly-cited papers within a rolling ~11-year window, NOT their whole
  career — HCA is designed to emulate HCR, so the counting window must match.
  This is distinct from: flat_works' 2000–2025 ingestion range, and the enclave
  pipeline's fixed 2014–2023 window (a different, unrelated 10-year analysis).
  The underlying HCW set (stage 3) itself is NOT restricted to this window — only
  the per-author count used for HCA qualification is.

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
from util import load_config, load_settings, guard

# NOTE: MAX_AUTHORS (hyper-authored/consortium exclusion) now lives upstream in
# pipeline/build_flat_works.py (value 29) — flat_works is the base work list for
# stage 1 below, so this file no longer needs its own copy of that filter.

# HCA qualification window: n_hcw (stage 4) counts only HCW published in this
# range, matching Clarivate's rolling ~11-year HCR citation window. Does NOT
# restrict which works can be a HCW in the first place (stage 1-3 stay 2000-2025).
HCA_YEAR_MIN = 2014
HCA_YEAR_MAX = 2024

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

def build_filtered_works_topics(flat_works_path: Path, oax: Path, out: Path,
                                auto_yes: bool = False) -> None:
    """
    One row per (work × topic) after applying the works filter.
    Columns: work_idx, publication_year, cited_by_count, authors_count,
             institutions_distinct_count, field_score, field_idx, field_name.

    Base work list now comes from flat_works (title dedup, type list,
    is_paratext/is_retracted, authors_count<=MAX_AUTHORS already applied
    there) instead of an independent raw-works scan. The one filter unique
    to HCA candidacy — cited_by_count > (2027 - publication_year) — is still
    applied here. Topic/field assignment (unnest work_topics, join
    topics.parquet) is unchanged from before this consolidation.

    NOTE: reading from flat_works also inherits its other membership
    requirements that this stage never had before: referenced_works_count>0,
    source type in the retained set (journal/conference/book series), and at
    least one qualifying-institution-type authorship. A highly-cited work
    failing any of those (e.g. zero references, hosted on a non-retained
    source type, or authored only by company/funder/archive-affiliated
    people) will no longer enter the HCA candidate pool, whereas it would
    have before. Expected to be rare among genuinely highly-cited works.

    Rebuilds only if stale relative to its inputs (see util/guard.py).
    """
    inputs = [str(flat_works_path), f'{oax}/work_topics/*.parquet', f'{oax}/topics.parquet']
    if not guard.ensure_fresh(out, *inputs, script=__file__, auto_yes=auto_yes,
                              label='filtered_works_topics'):
        return

    print('  Building filtered_works_topics.parquet ...')
    t0 = time.time()
    con = _con(out.parent)
    con.execute(f"""
    COPY (
      WITH
      dedup_works AS (
        SELECT DISTINCT work_idx, title, publication_year, cited_by_count,
               authors_count, institutions_distinct_count
        FROM '{flat_works_path}'
        WHERE cited_by_count > (2027 - publication_year)
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
    guard.record_build(out, *inputs, script=__file__, build_seconds=time.time() - t0)
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    w = (duckdb.execute(f"SELECT COUNT(DISTINCT work_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  filtered_works_topics: {n:,} rows, {w:,} works  [{time.time()-t0:.0f}s]')


# ── Stage 2: hcw_flat_works ───────────────────────────────────────────────────

def build_hcw_flat_works(topics_path: Path, out: Path, auto_yes: bool = False) -> None:
    """
    One row per (work × field) with normalised field_share.
    Columns: work_idx, publication_year, cited_by_count, authors_count,
             institutions_distinct_count, field_idx, field_name, field_share.
    No authors_count filter here — flat_works already enforces
    authors_count <= MAX_AUTHORS (29) upstream, in build_flat_works.py.
    Rebuilds only if stale relative to its inputs (see util/guard.py).
    """
    if not guard.ensure_fresh(out, str(topics_path), script=__file__, auto_yes=auto_yes,
                              label='hcw_flat_works'):
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
      GROUP BY work_idx, publication_year, cited_by_count,
               authors_count, institutions_distinct_count,
               field_idx, field_name
    ) TO '{out}' (FORMAT PARQUET)
    """)
    con.close()
    guard.record_build(out, str(topics_path), script=__file__, build_seconds=time.time() - t0)
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    w = (duckdb.execute(f"SELECT COUNT(DISTINCT work_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_flat_works: {n:,} rows, {w:,} works  [{time.time()-t0:.0f}s]')


# ── Stage 3: hcw_works ────────────────────────────────────────────────────────

def build_hcw_works(flat_path: Path, out: Path, auto_yes: bool = False) -> None:
    """
    Top-1% of cited_by_count per (field_idx, publication_year) from hcw_flat_works.
    Columns: work_idx, publication_year, cited_by_count, field_idx, field_name, field_share.
    Rebuilds only if stale relative to its inputs (see util/guard.py).
    """
    if not guard.ensure_fresh(out, str(flat_path), script=__file__, auto_yes=auto_yes,
                              label='hcw_works'):
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
    guard.record_build(out, str(flat_path), script=__file__, build_seconds=time.time() - t0)
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    w = (duckdb.execute(f"SELECT COUNT(DISTINCT work_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_works: {n:,} rows, {w:,} works  [{time.time()-t0:.0f}s]')


# ── Stage 4: hcw_authors ──────────────────────────────────────────────────────

def build_hcw_authors(hcw_path: Path, oax: Path, out: Path, auto_yes: bool = False) -> None:
    """
    Join HCW works to authorships; aggregate to (author × field) with metadata.
    Hyper-authored works are already excluded upstream (flat_works, MAX_AUTHORS=29).
    n_hcw (and the modal institution derived alongside it) counts only HCW with
    publication_year in [HCA_YEAR_MIN, HCA_YEAR_MAX] — the Clarivate-emulating
    HCA qualification window. The underlying HCW set itself is not restricted;
    only this per-author aggregation is.
    Rebuilds only if stale relative to its inputs (see util/guard.py).
    """
    inputs = [str(hcw_path), f'{oax}/institutions.parquet',
              f'{oax}/authorships/*.parquet', f'{oax}/authors/*.parquet']
    if not guard.ensure_fresh(out, *inputs, script=__file__, auto_yes=auto_yes,
                              label='hcw_authors'):
        return

    print('  Building hcw_authors.parquet ...')
    t0 = time.time()
    con = _con(out.parent)

    con.execute(f"""
    CREATE TEMP TABLE _hcw AS
    SELECT work_idx, field_idx FROM '{hcw_path}'
    WHERE publication_year BETWEEN {HCA_YEAR_MIN} AND {HCA_YEAR_MAX}
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
    guard.record_build(out, *inputs, script=__file__, build_seconds=time.time() - t0)
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    a = (duckdb.execute(f"SELECT COUNT(DISTINCT author_idx) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_authors: {n:,} rows, {a:,} distinct authors  [{time.time()-t0:.0f}s]')


# ── Stage 5: hcw_authorships ─────────────────────────────────────────────────

def build_hcw_authorships(hcw_path: Path, oax: Path, out: Path, auto_yes: bool = False) -> None:
    """
    All (work_idx, author_idx) pairs for HCW works.
    Used for co-author overlap analysis in the clustering pipeline.
    Rebuilds only if stale relative to its inputs (see util/guard.py).
    """
    inputs = [str(hcw_path), f'{oax}/authorships/*.parquet']
    if not guard.ensure_fresh(out, *inputs, script=__file__, auto_yes=auto_yes,
                              label='hcw_authorships'):
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
    guard.record_build(out, *inputs, script=__file__, build_seconds=time.time() - t0)
    n = (duckdb.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone() or (0,))[0]
    print(f'  hcw_authorships: {n:,} rows  [{time.time()-t0:.0f}s]')


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--yes', '-y', action='store_true',
                         help='Rebuild stale outputs without prompting')
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()
    oa    = paths.openalex / 'parquet'
    w     = paths.working

    flat_works_path  = w / f'flat_works_{settings.year_min}_{settings.year_max}.parquet'
    topics_path      = w / 'filtered_works_topics.parquet'
    flat_path        = w / 'hcw_flat_works.parquet'
    hcw_path         = w / 'hcw_works.parquet'
    authors_path     = w / 'hcw_authors.parquet'
    authorships_path = w / 'hcw_authorships.parquet'

    if not flat_works_path.exists():
        print(f'ERROR: {flat_works_path} not found — run pipeline/build_flat_works.py first')
        sys.exit(1)

    print('Stage 1: filtered_works_topics')
    build_filtered_works_topics(flat_works_path, oa, topics_path, auto_yes=args.yes)

    print('Stage 2: hcw_flat_works')
    build_hcw_flat_works(topics_path, flat_path, auto_yes=args.yes)

    print('Stage 3: hcw_works  (top 1% per field × year)')
    build_hcw_works(flat_path, hcw_path, auto_yes=args.yes)

    print('Stage 4: hcw_authors')
    build_hcw_authors(hcw_path, oa, authors_path, auto_yes=args.yes)

    print('Stage 5: hcw_authorships')
    build_hcw_authorships(hcw_path, oa, authorships_path, auto_yes=args.yes)

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
